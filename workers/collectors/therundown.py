"""TheRundown collector — primary odds source (v2 API).

Auth: `X-TheRundown-Key` header. Base: https://therundown.io/api/v2
Docs: https://docs.therundown.io  ·  Samples: docs/providers/samples/therundown_*.json
Payload shapes verified against captured samples on 2026-07-11 (CLAUDE.md §14).

Coverage notes (verified 2026-07-11):
- NO esports, NO Liga I, NO Eredivisie in /sports — see docs/providers/therundown.md.
- Free tier books: DraftKings (19), BetMGM (22), FanDuel (23). Pinnacle (3)
  requires the Starter plan — the free tier has NO sharp reference.
- Prices are AMERICAN odds; converted to decimal at the parse boundary.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta

import httpx

from core.config import provider_api_key
from core.types import Fixture, OddsSnapshot

PROVIDER = "therundown"
BASE_URL = "https://therundown.io/api/v2"

# TheRundown sport_id -> our sports.id (soccer competitions only for now;
# see docs page for the full /sports list — there is no esports coverage).
SPORT_MAP: dict[int, str] = {
    10: "football",  # MLS
    11: "football",  # EPL
    12: "football",  # Ligue 1
    13: "football",  # Bundesliga
    14: "football",  # La Liga
    15: "football",  # Serie A
    16: "football",  # UEFA Champions League
    17: "football",  # UEFA Euro
    18: "football",  # FIFA World Cup
    19: "football",  # J1 League
    33: "football",  # UEFA Europa League
    34: "football",  # Liga MX
}

# markets[].market_id -> canonical market key (packages/shared/markets.json)
MARKET_MAP: dict[int, str] = {
    1: "h2h",  # "moneyline"
    2: "asian_handicap",  # "handicap"
    3: "totals",  # "totals"
}

_FULL_TIME_PERIOD = 0


def american_to_decimal(price: int | float) -> float:
    """-105 -> 1.952…, +270 -> 3.70. Zero is invalid."""
    a = float(price)
    if a == 0:
        raise ValueError("american odds of 0 are invalid")
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


class TheRundownCollector:
    provider = PROVIDER

    def __init__(
        self,
        client: httpx.Client | None = None,
        api_key: str | None = None,
        sport_ids: list[int] | None = None,
        affiliates: dict[int, str] | None = None,
        main_lines_only: bool = True,
    ) -> None:
        self._client = client or httpx.Client(
            base_url=BASE_URL,
            headers={"X-TheRundown-Key": api_key or provider_api_key(PROVIDER)},
            timeout=30.0,
        )
        self._sport_ids = sport_ids or list(SPORT_MAP)
        self._affiliates = affiliates  # id -> lowercase bookmaker name
        self._main_lines_only = main_lines_only

    # -- HTTP ---------------------------------------------------------------

    def _get(self, path: str) -> dict:
        # Free tier rate-limits back-to-back calls (429 observed 2026-07-11
        # on consecutive daily events requests). Bounded backoff.
        for attempt in range(3):
            resp = self._client.get(path)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp.json()
            retry_after = float(resp.headers.get("Retry-After", 10 * (attempt + 1)))
            time.sleep(min(retry_after, 60.0))
        resp.raise_for_status()
        return resp.json()  # pragma: no cover - unreachable, raise above fires

    def affiliates(self) -> dict[int, str]:
        """affiliate_id -> lowercase name. Free reference endpoint; cached."""
        if self._affiliates is None:
            data = self._get("/affiliates")
            self._affiliates = {
                a["affiliate_id"]: a["affiliate_name"].lower().replace(" ", "_")
                for a in data["affiliates"]
            }
        return self._affiliates

    def events(self, sport_id: int, day: date) -> dict:
        return self._get(f"/sports/{sport_id}/events/{day.isoformat()}")

    # -- OddsCollector ------------------------------------------------------

    def poll(self, since: datetime, until: datetime) -> tuple[list[Fixture], list[OddsSnapshot]]:
        fixtures: list[Fixture] = []
        snapshots: list[OddsSnapshot] = []
        days = _days_between(since, until)
        for sport_id in self._sport_ids:
            for day in days:
                payload = self.events(sport_id, day)
                for event in payload.get("events", []):
                    fx = self.parse_fixture(event)
                    if fx is None or not since <= fx.commence_time <= until:
                        continue
                    fixtures.append(fx)
                    snapshots.extend(self.parse_snapshots(event))
        return fixtures, snapshots

    # -- Parsing (shapes per samples/therundown_events_fifa.json) ------------

    def parse_fixture(self, event: dict) -> Fixture | None:
        sport = SPORT_MAP.get(event["sport_id"])
        if sport is None:
            return None
        home = away = None
        for team in event.get("teams_normalized", event.get("teams", [])):
            if team.get("is_home"):
                home = team["name"]
            elif team.get("is_away"):
                away = team["name"]
        if not home or not away:
            return None
        return Fixture(
            provider=PROVIDER,
            provider_key=event["event_id"],
            sport_id=sport,
            home_name=home,
            away_name=away,
            commence_time=datetime.fromisoformat(event["event_date"]),
        )

    def parse_snapshots(self, event: dict) -> list[OddsSnapshot]:
        """markets[] -> participants[] -> lines[] -> prices{affiliate_id: {...}}.

        Outcome naming: TYPE_TEAM participants map to home/away via the event's
        team ids; TYPE_RESULT participants are 'Draw', 'Over', 'Under'.
        """
        affiliates = self.affiliates()
        captured_at = datetime.now(UTC)
        home_id, away_id = _team_ids(event)
        out: list[OddsSnapshot] = []
        for market in event.get("markets", []):
            market_key = MARKET_MAP.get(market["market_id"])
            if market_key is None or market["period_id"] != _FULL_TIME_PERIOD:
                continue
            for participant in market.get("participants", []):
                outcome = _outcome_name(participant, home_id, away_id)
                if outcome is None:
                    continue
                for line in participant.get("lines", []):
                    line_value = float(line["value"]) if "value" in line else None
                    for aff_id_str, price_obj in line.get("prices", {}).items():
                        if self._main_lines_only and not price_obj.get("is_main_line"):
                            continue
                        bookmaker = affiliates.get(int(aff_id_str))
                        if bookmaker is None:
                            continue
                        out.append(
                            OddsSnapshot(
                                provider=PROVIDER,
                                event_provider_key=event["event_id"],
                                bookmaker=bookmaker,
                                market=market_key,
                                outcome=outcome,
                                price=american_to_decimal(price_obj["price"]),
                                line=line_value,
                                captured_at=captured_at,
                            )
                        )
        return out


def _team_ids(event: dict) -> tuple[int | None, int | None]:
    home_id = away_id = None
    for team in event.get("teams", []):
        if team.get("is_home"):
            home_id = team["team_id"]
        elif team.get("is_away"):
            away_id = team["team_id"]
    return home_id, away_id


def _outcome_name(participant: dict, home_id: int | None, away_id: int | None) -> str | None:
    if participant["type"] == "TYPE_TEAM":
        if participant["id"] == home_id:
            return "home"
        if participant["id"] == away_id:
            return "away"
        return None
    if participant["type"] == "TYPE_RESULT":
        name = participant["name"].lower()
        return name if name in ("draw", "over", "under") else None
    return None


def _days_between(since: datetime, until: datetime) -> list[date]:
    day = since.astimezone(UTC).date()
    end = until.astimezone(UTC).date()
    days = []
    while day <= end:
        days.append(day)
        day += timedelta(days=1)
    return days
