"""PandaScore SportAdapter — CS2 fixtures/results (stats side, never odds).

Auth: `Authorization: Bearer` header. Base: https://api.pandascore.co
Payload shapes verified against captured samples on 2026-07-12 (CLAUDE.md §14):
  docs/providers/samples/pandascore_matches_upcoming_cs2.json
  docs/providers/samples/pandascore_matches_past_cs2.json

Quirks (documented in docs/providers/pandascore.md):
- Esports has no home/away: `opponents` order is PandaScore's own and does NOT
  match other providers. Results therefore carry team NAMES so persist_results
  can align scores with the event's resolved orientation (never by position).
- Timestamps can be null even on finished matches — begin_at falls back to
  scheduled_at then original_scheduled_at, and one sample row has ALL three
  null. Timestamp-less results are still returned (they cannot be window
  filtered; persist_results only attaches known events, so no junk risk).
- Scores live in `results[{score, team_id}]`, matched to opponents by team_id.
- ToS (§5): free tier is prototyping-only for betting products. Get written
  clarity before any commercial launch on this data.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx

from core.config import provider_api_key
from core.types import Fixture, Result

PROVIDER = "pandascore"
SPORT_ID = "cs2"
BASE_URL = "https://api.pandascore.co"
# PandaScore still exposes CS2 under the legacy `csgo` path (verified: the
# samples above came from /csgo/matches/* and carry current CS2 fixtures).
VIDEOGAME_PATH = "csgo"


def _commence_time(match: dict) -> datetime | None:
    """begin_at is null on some rows (even finished ones) — fall back."""
    ts = match.get("begin_at") or match.get("scheduled_at") or match.get("original_scheduled_at")
    return datetime.fromisoformat(ts) if ts else None


def _opponent_names(match: dict) -> tuple[str, str] | None:
    opponents = [(o or {}).get("opponent") or {} for o in match.get("opponents", [])]
    if len(opponents) != 2:
        return None  # TBD slot (bracket not settled) — skip until both known
    first, second = opponents[0].get("name"), opponents[1].get("name")
    if not first or not second:
        return None
    return first, second


def parse_fixture(match: dict) -> Fixture | None:
    names = _opponent_names(match)
    commence = _commence_time(match)
    if names is None or commence is None:
        return None
    return Fixture(
        provider=PROVIDER,
        provider_key=str(match["id"]),
        sport_id=SPORT_ID,
        competition_name=match["league"]["name"],
        home_name=names[0],
        away_name=names[1],
        commence_time=commence,
    )


def parse_result(match: dict) -> Result | None:
    """Finished match -> Result, scores matched to opponents via team_id.

    Draws are kept (a BO2 can end 1-1; h2h grades it as a push). Forfeits are
    kept too — PandaScore still reports a score/winner — flagged in raw_status.
    """
    if match.get("status") != "finished":
        return None
    names = _opponent_names(match)
    if names is None:
        return None
    ids = [(o["opponent"] or {}).get("id") for o in match["opponents"]]
    scores = {r["team_id"]: r["score"] for r in match.get("results", [])}
    if ids[0] not in scores or ids[1] not in scores:
        return None  # defensive: finished but scores incomplete
    finished_at = match.get("end_at")
    return Result(
        provider=PROVIDER,
        provider_key=str(match["id"]),
        sport_id=SPORT_ID,
        home_score=int(scores[ids[0]]),
        away_score=int(scores[ids[1]]),
        home_name=names[0],
        away_name=names[1],
        finished_at=datetime.fromisoformat(finished_at) if finished_at else None,
        raw_status="forfeit" if match.get("forfeit") else "finished",
    )


class PandaScoreAdapter:
    """SportAdapter for CS2 via PandaScore (free tier: 1,000 req/hr).

    Each `fixtures` or `results` call costs one request. Page size covers the
    scheduler's window comfortably (50 matches ≈ several days of tier-1 CS2).
    """

    sport_id = SPORT_ID

    def __init__(
        self,
        client: httpx.Client | None = None,
        api_key: str | None = None,
        per_page: int = 50,
    ) -> None:
        self._per_page = per_page
        key = api_key or provider_api_key(PROVIDER)
        self._client = client or httpx.Client(
            base_url=BASE_URL, timeout=30.0, headers={"Authorization": f"Bearer {key}"}
        )

    # -- HTTP ---------------------------------------------------------------

    def _get(self, path: str, **params: str | int) -> list[dict]:
        for attempt in range(3):
            resp = self._client.get(path, params=params)
            if resp.status_code != 429:
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else []
            retry_after = float(resp.headers.get("Retry-After", 10 * (attempt + 1)))
            time.sleep(min(retry_after, 60.0))
        resp.raise_for_status()
        return []  # pragma: no cover - unreachable, raise above fires

    # -- SportAdapter ---------------------------------------------------------

    def fixtures(self, since: datetime, until: datetime) -> list[Fixture]:
        """Upcoming matches filtered to the window (client-side: the captured
        samples only verify per_page — do not assume range params, §14)."""
        items = self._get(f"/{VIDEOGAME_PATH}/matches/upcoming", per_page=self._per_page)
        out: list[Fixture] = []
        for item in items:
            fx = parse_fixture(item)
            if fx is not None and since <= fx.commence_time <= until:
                out.append(fx)
        return out

    def results(self, since: datetime) -> list[Result]:
        """Finished matches that started at or after `since` (newest first
        from the provider; one page covers the grader's lookback). Matches
        with no usable timestamp (observed in the sample) are kept: they are
        on the recent-past page and persist_results only attaches events we
        already ingested."""
        items = self._get(f"/{VIDEOGAME_PATH}/matches/past", per_page=self._per_page)
        out: list[Result] = []
        for item in items:
            res = parse_result(item)
            if res is None:
                continue
            commence = _commence_time(item)
            if commence is None or commence.astimezone(UTC) >= since:
                out.append(res)
        return out

    def stats(self, event_id: str) -> dict:
        """Raw provider stats for FeatureModules (Phase 2+). Not implemented
        until a per-match statistics sample is captured (§14)."""
        raise NotImplementedError("capture a statistics sample before implementing (§14)")
