"""OddsPapi collector — esports odds (Pinnacle CS2 verified) + backup source.

Auth: `apiKey` query param. Base: https://api.oddspapi.io/v4
Payload shapes verified against captured samples on 2026-07-11 (CLAUDE.md §14):
docs/providers/samples/oddspapi_{fixtures,odds,markets,tournaments,sports}_*.json

Quirks (verified 2026-07-11, documented in docs/providers/oddspapi.md):
- /odds-by-tournaments requires EXACTLY ONE `bookmaker` per request (400 otherwise),
  so each extra bookmaker costs one request from the ~250 req/mo free budget.
- /fixtures requires the SINGULAR `tournamentId` param (400 with `tournamentIds`).
- Odds payloads carry participant IDs but NOT names; names come from /fixtures.
- Prices are DECIMAL already. `mainLine` flags the main handicap/total.
- The /markets reference is the source of truth for what a marketId means
  (marketType, period, handicap, outcome names). ~9MB raw; a trimmed copy for
  sports 10/16/17/61 lives in samples/oddspapi_markets.json.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from core.config import provider_api_key
from core.types import Fixture, OddsSnapshot

PROVIDER = "oddspapi"
BASE_URL = "https://api.oddspapi.io/v4"

# OddsPapi sportId -> our sports.id
SPORT_MAP: dict[int, str] = {
    10: "football",
    16: "dota2",
    17: "cs2",
    61: "valorant",
}

_ESPORTS = frozenset({16, 17, 61})

# Only full-match markets for now; per-map/period markets (p1..p5) are skipped.
_FULL_MATCH_PERIODS = frozenset({"result", "fulltime"})

# (is_esport, marketType) -> canonical market key (packages/shared/markets.json)
_MARKET_TYPE_MAP: dict[tuple[bool, str], str] = {
    (False, "1x2"): "h2h",
    (False, "moneyline"): "h2h",
    (False, "totals"): "totals",
    (False, "spreads"): "asian_handicap",
    (False, "bothteamsscore"): "btts",
    (True, "moneyline"): "h2h",
    (True, "totals"): "total_maps",
    (True, "spreads"): "map_handicap",
}

_LINE_MARKET_TYPES = frozenset({"totals", "spreads"})

# Reference outcomeName -> canonical outcome
_OUTCOME_NAME_MAP: dict[str, str] = {
    "1": "home",
    "2": "away",
    "x": "draw",
    "over": "over",
    "under": "under",
    "yes": "yes",
    "no": "no",
}


def load_markets_ref(path: str | Path) -> list[dict]:
    """Load a saved /markets reference payload (e.g. the trimmed sample)."""
    return json.loads(Path(path).read_text())


def select_active_tournaments(tournaments: list[dict], limit: int) -> list[int]:
    """tournamentIds with live/upcoming fixtures, busiest first. /tournaments
    rows carry live counts (futureFixtures/upcomingFixtures/liveFixtures per
    samples/oddspapi_tournaments_cs2.json) — hardcoding an id goes stale the
    moment an event ends (observed 2026-07-12: finished tournament -> 404)."""
    scored = sorted(
        (
            (
                t.get("liveFixtures", 0)
                + t.get("upcomingFixtures", 0)
                + t.get("futureFixtures", 0),
                int(t["tournamentId"]),
            )
            for t in tournaments
        ),
        reverse=True,
    )
    return [tid for count, tid in scored[:limit] if count > 0]


class OddsPapiCollector:
    provider = PROVIDER

    def __init__(
        self,
        tournament_ids: list[int] | None = None,
        bookmakers: list[str] | None = None,
        client: httpx.Client | None = None,
        api_key: str | None = None,
        markets_ref: list[dict] | None = None,
        main_lines_only: bool = True,
        discover_sport_ids: list[int] | None = None,
        max_tournaments: int = 2,
    ) -> None:
        # Static ids (tests, one-off captures) OR discovery per poll from
        # /tournaments (default: CS2). Discovery costs 1 request per sport per
        # poll and caps at `max_tournaments` to protect the ~250 req/mo budget.
        self._tournament_ids = tournament_ids
        self._discover_sport_ids = discover_sport_ids or [17]  # 17 = CS2
        self._max_tournaments = max_tournaments
        # One request PER bookmaker per odds call — keep this list short.
        self._bookmakers = bookmakers or ["pinnacle"]
        self._api_key = api_key or provider_api_key(PROVIDER)
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=30.0)
        self._main_lines_only = main_lines_only
        self._markets_by_id: dict[int, dict] | None = (
            {int(m["marketId"]): m for m in markets_ref} if markets_ref is not None else None
        )

    # -- HTTP ---------------------------------------------------------------

    def _get(self, path: str, **params: str | int) -> list | dict:
        # Free tier rate-limits back-to-back calls (429 observed 2026-07-11
        # on the 2nd odds request). Bounded backoff, honoring Retry-After.
        for attempt in range(3):
            resp = self._client.get(path, params={**params, "apiKey": self._api_key})
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp.json()
            retry_after = float(resp.headers.get("Retry-After", 10 * (attempt + 1)))
            time.sleep(min(retry_after, 60.0))
        resp.raise_for_status()
        return resp.json()  # pragma: no cover - unreachable, raise above fires

    def markets_ref(self) -> dict[int, dict]:
        """marketId -> market reference row. Fetched once and cached; prefer
        passing `markets_ref=` from a saved copy to spare the request budget."""
        if self._markets_by_id is None:
            data = self._get("/markets")
            self._markets_by_id = {int(m["marketId"]): m for m in data}
        return self._markets_by_id

    def fixtures_for_tournament(self, tournament_id: int) -> list[dict]:
        # Singular `tournamentId` — the plural form is rejected (400).
        data = self._get("/fixtures", tournamentId=tournament_id)
        return data if isinstance(data, list) else []

    def tournaments_for_sport(self, sport_id: int) -> list[dict]:
        data = self._get("/tournaments", sportId=sport_id)
        return data if isinstance(data, list) else []

    def odds_by_tournaments(self, bookmaker: str, tournament_ids: list[int]) -> list[dict]:
        # Exactly one `bookmaker` per request (400 otherwise).
        data = self._get(
            "/odds-by-tournaments",
            tournamentIds=",".join(str(t) for t in tournament_ids),
            bookmaker=bookmaker,
        )
        return data if isinstance(data, list) else []

    def _resolve_tournament_ids(self) -> list[int]:
        """Static ids if configured, else the busiest active tournaments."""
        if self._tournament_ids:
            return self._tournament_ids
        tournaments: list[dict] = []
        for sport_id in self._discover_sport_ids:
            tournaments.extend(self.tournaments_for_sport(sport_id))
        return select_active_tournaments(tournaments, self._max_tournaments)

    # -- OddsCollector ------------------------------------------------------

    def poll(self, since: datetime, until: datetime) -> tuple[list[Fixture], list[OddsSnapshot]]:
        tournament_ids = self._resolve_tournament_ids()
        if not tournament_ids:
            return [], []  # nothing active -> skip the odds request entirely

        fixtures: list[Fixture] = []
        in_window: set[str] = set()
        for tournament_id in tournament_ids:
            for raw in self.fixtures_for_tournament(tournament_id):
                fx = self.parse_fixture(raw)
                if fx is None or not since <= fx.commence_time <= until:
                    continue
                fixtures.append(fx)
                in_window.add(fx.provider_key)

        snapshots: list[OddsSnapshot] = []
        for bookmaker in self._bookmakers:
            for raw in self.odds_by_tournaments(bookmaker, tournament_ids):
                if raw.get("fixtureId") in in_window:
                    snapshots.extend(self.parse_snapshots(raw))
        return fixtures, snapshots

    # -- Parsing (shapes per samples/oddspapi_*_cs2_blast.json) --------------

    def parse_fixture(self, raw: dict) -> Fixture | None:
        sport = SPORT_MAP.get(raw["sportId"])
        home = raw.get("participant1Name")
        away = raw.get("participant2Name")
        if sport is None or not home or not away:
            return None
        return Fixture(
            provider=PROVIDER,
            provider_key=raw["fixtureId"],
            sport_id=sport,
            home_name=home,
            away_name=away,
            commence_time=datetime.fromisoformat(raw["startTime"]),
        )

    def parse_snapshots(self, raw: dict) -> list[OddsSnapshot]:
        """bookmakerOdds{book} -> markets{marketId} -> outcomes{outcomeId} ->
        players{"0"} -> {price, mainLine, active}. Market semantics resolved
        via the /markets reference (marketType, period, handicap, outcomes)."""
        if SPORT_MAP.get(raw["sportId"]) is None:
            return []
        is_esport = raw["sportId"] in _ESPORTS
        ref = self.markets_ref()
        captured_at = datetime.now(UTC)
        out: list[OddsSnapshot] = []
        for book_name, book in raw.get("bookmakerOdds", {}).items():
            if book.get("suspended") or not book.get("bookmakerIsActive", True):
                continue
            for market_id_str, market in book.get("markets", {}).items():
                if not market.get("marketActive", True):
                    continue
                mref = ref.get(int(market_id_str))
                if mref is None or mref.get("playerProp"):
                    continue
                if mref["period"] not in _FULL_MATCH_PERIODS:
                    continue
                market_key = _MARKET_TYPE_MAP.get((is_esport, mref["marketType"]))
                if market_key is None:
                    continue
                line = float(mref["handicap"]) if mref["marketType"] in _LINE_MARKET_TYPES else None
                outcome_names = {
                    int(o["outcomeId"]): _OUTCOME_NAME_MAP.get(o["outcomeName"].lower())
                    for o in mref["outcomes"]
                }
                for outcome_id_str, outcome in market.get("outcomes", {}).items():
                    canonical = outcome_names.get(int(outcome_id_str))
                    if canonical is None:
                        continue
                    for player in outcome.get("players", {}).values():
                        if not player.get("active") or player.get("playerName") is not None:
                            continue
                        if self._main_lines_only and not player.get("mainLine"):
                            continue
                        out.append(
                            OddsSnapshot(
                                provider=PROVIDER,
                                event_provider_key=raw["fixtureId"],
                                bookmaker=book_name.lower(),
                                market=market_key,
                                outcome=canonical,
                                price=float(player["price"]),
                                line=line,
                                captured_at=captured_at,
                            )
                        )
        return out
