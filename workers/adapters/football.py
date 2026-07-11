"""API-Football SportAdapter — fixtures/results for football (Liga I first).

Auth: `x-apisports-key` header. Base: https://v3.football.api-sports.io
Payload shapes verified against captured samples on 2026-07-11 (CLAUDE.md §14):
  docs/providers/samples/api_football_fixtures_liga1.json  (?league&season&next=N)
  docs/providers/samples/api_football_results_liga1.json   (?league&season&last=N)

Quirks (documented in docs/providers/api_football.md):
- Errors come back as HTTP 200 with a non-empty `errors` field — must be checked.
- Daily quota (100 free / 7,500 Pro) resets 00:00 UTC.
- Odds endpoints refresh ~3h — NEVER use this provider for lines or CLV (§5).
- `score.fulltime` is the 90-minute score even for AET/PEN finishes (verified in
  the sample: AET row shows goals 4-3 but fulltime 3-3). 1X2/h2h markets settle
  on the 90-minute score, so Result carries `score.fulltime`, not `goals`.
- Statuses observed for finished matches: FT, AET, PEN.

This adapter provides STATS-SIDE data only (fixtures, results, later stats for
FeatureModules). Odds always come from the odds collectors.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx

from core.config import provider_api_key
from core.types import Fixture, Result

PROVIDER = "api_football"
SPORT_ID = "football"
BASE_URL = "https://v3.football.api-sports.io"

LIGA_I_LEAGUE_ID = 283  # verified via samples/api_football_leagues_ro.json

# fixture.status.short values that mean "finished, gradeable".
FINISHED_STATUSES = frozenset({"FT", "AET", "PEN"})


def parse_fixture(item: dict) -> Fixture:
    """One element of `response` from /fixtures -> provider-agnostic Fixture."""
    fx = item["fixture"]
    return Fixture(
        provider=PROVIDER,
        provider_key=str(fx["id"]),
        sport_id=SPORT_ID,
        competition_name=item["league"]["name"],
        home_name=item["teams"]["home"]["name"],
        away_name=item["teams"]["away"]["name"],
        commence_time=datetime.fromisoformat(fx["date"]),
    )


def parse_result(item: dict) -> Result | None:
    """One element of `response` -> Result, or None if not finished.

    Scores are the 90-minute (`score.fulltime`) numbers: that is what 1X2/h2h
    and totals markets settle on, regardless of extra time or penalties.
    """
    status = item["fixture"]["status"]["short"]
    if status not in FINISHED_STATUSES:
        return None
    fulltime = item["score"]["fulltime"]
    if fulltime["home"] is None or fulltime["away"] is None:
        return None  # defensive: finished status but no 90-min score
    return Result(
        provider=PROVIDER,
        provider_key=str(item["fixture"]["id"]),
        sport_id=SPORT_ID,
        home_score=int(fulltime["home"]),
        away_score=int(fulltime["away"]),
        raw_status=status,
    )


class ApiFootballAdapter:
    """SportAdapter for football via API-Football.

    One instance covers a set of leagues for one season. Each `fixtures` or
    `results` call costs one request per league from the daily quota.
    """

    sport_id = SPORT_ID

    def __init__(
        self,
        league_ids: list[int],
        season: int,
        client: httpx.Client | None = None,
        api_key: str | None = None,
        lookahead_count: int = 50,
        lookback_count: int = 50,
    ) -> None:
        self._league_ids = league_ids
        self._season = season
        self._lookahead = lookahead_count
        self._lookback = lookback_count
        key = api_key or provider_api_key(PROVIDER)
        self._client = client or httpx.Client(
            base_url=BASE_URL, timeout=30.0, headers={"x-apisports-key": key}
        )

    # -- HTTP ---------------------------------------------------------------

    def _get(self, path: str, **params: str | int) -> list[dict]:
        for attempt in range(3):
            resp = self._client.get(path, params=params)
            if resp.status_code != 429:
                resp.raise_for_status()
                body = resp.json()
                # API-Football reports problems as HTTP 200 + non-empty errors.
                if body.get("errors"):
                    raise RuntimeError(f"api_football {path} errors: {body['errors']}")
                return body.get("response", [])
            retry_after = float(resp.headers.get("Retry-After", 10 * (attempt + 1)))
            time.sleep(min(retry_after, 60.0))
        resp.raise_for_status()
        return []  # pragma: no cover - unreachable, raise above fires

    # -- SportAdapter ---------------------------------------------------------

    def fixtures(self, since: datetime, until: datetime) -> list[Fixture]:
        """Upcoming fixtures across configured leagues, filtered to the window.

        Uses the verified `next=N` param and filters client-side (the from/to
        params are not in our captured samples — do not assume them, §14)."""
        out: list[Fixture] = []
        for league_id in self._league_ids:
            items = self._get(
                "/fixtures", league=league_id, season=self._season, next=self._lookahead
            )
            for item in items:
                fx = parse_fixture(item)
                if since <= fx.commence_time <= until:
                    out.append(fx)
        return out

    def results(self, since: datetime) -> list[Result]:
        """Finished results (FT/AET/PEN) with kickoff at or after `since`."""
        out: list[Result] = []
        for league_id in self._league_ids:
            items = self._get(
                "/fixtures", league=league_id, season=self._season, last=self._lookback
            )
            for item in items:
                res = parse_result(item)
                if res is None:
                    continue
                kickoff = datetime.fromisoformat(item["fixture"]["date"]).astimezone(UTC)
                if kickoff >= since:
                    out.append(res)
        return out

    def stats(self, event_id: str) -> dict:
        """Raw provider stats for FeatureModules (Phase 2+). Not implemented
        until a /fixtures/statistics sample is captured (§14)."""
        raise NotImplementedError("capture a statistics sample before implementing (§14)")
