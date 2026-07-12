"""Daily Best Bets feed builder (pivot ADR 0002; CLAUDE.md §13 Task 2).

The public identity of the product: once per UTC day, freeze a small ranked
selection of the day's picks. Candidates are ALREADY-published immutable picks
(this module never publishes anything):

- ``verified``    — picks from models with status ``public`` (§9 gate-passed).
                    Only this label may carry the verified badge.
- ``market_edge`` — Layer-1 market-model picks (status ``shadow``) that cleared
                    the §8 EV threshold at publish time. Clearly labeled as
                    unverified signals; the two labels must never blur.

Selection: verified first, then by published EV; at most one entry per event
(correlated markets on the same match are deduped); capped at FEED_MAX_PICKS
(config, default 8). The stored feed is immutable per date (append-only
trigger): re-runs are no-ops, and picks published after the freeze appear on
the board and track record but not in an already-frozen feed.

Run standalone: uv run python -m engine.daily_feed
Scheduled: tools.run_collectors builds it once per day after
FEED_BUILD_HOUR_UTC (default 06:00 UTC).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

from core.db import connect

LABEL_VERIFIED = "verified"
LABEL_MARKET_EDGE = "market_edge"

DEFAULT_MAX_PICKS = 8


def max_picks_from_env() -> int:
    return int(os.environ.get("FEED_MAX_PICKS", str(DEFAULT_MAX_PICKS)))


def label_for(model_status: str) -> str:
    """Gate-passed models (§9) and ONLY those get the verified label."""
    return LABEL_VERIFIED if model_status == "public" else LABEL_MARKET_EDGE


@dataclass(frozen=True)
class FeedCandidate:
    pick_id: Any  # UUID in production, any hashable in tests
    event_id: Any
    model_status: str
    ev: float


def select_feed(
    candidates: list[FeedCandidate], max_picks: int = DEFAULT_MAX_PICKS
) -> list[FeedCandidate]:
    """Pure ranking: verified picks first, then by EV descending; at most one
    entry per event (correlated-market dedupe); capped at max_picks."""
    ranked = sorted(
        candidates,
        key=lambda c: (label_for(c.model_status) != LABEL_VERIFIED, -c.ev),
    )
    out: list[FeedCandidate] = []
    seen_events: set[Any] = set()
    for c in ranked:
        if c.event_id in seen_events:
            continue
        seen_events.add(c.event_id)
        out.append(c)
        if len(out) >= max_picks:
            break
    return out


def _candidates(conn: psycopg.Connection, feed_date: date, now: datetime) -> list[FeedCandidate]:
    """Pre-match picks on events commencing on feed_date (UTC). EV comes from
    the exact pick_features payload the publisher stored (§2.1)."""
    day_start = datetime(feed_date.year, feed_date.month, feed_date.day, tzinfo=UTC)
    with conn.cursor() as cur:
        cur.execute(
            """
            select p.id, p.event_id, m.status,
                   coalesce((pf.payload ->> 'ev')::float8, 0.0) as ev
            from picks p
            join models m on m.id = p.model_id
            join events e on e.id = p.event_id
            left join pick_features pf on pf.pick_id = p.id
            where m.status in ('public', 'shadow')
              and e.status = 'scheduled'
              and e.commence_time > %(now)s
              and e.commence_time >= %(day_start)s
              and e.commence_time < %(day_end)s
            """,
            {
                "now": now,
                "day_start": day_start,
                "day_end": day_start + timedelta(days=1),
            },
        )
        return [
            FeedCandidate(pick_id=r[0], event_id=r[1], model_status=r[2], ev=float(r[3]))
            for r in cur.fetchall()
        ]


def build_daily_feed(
    conn: psycopg.Connection,
    feed_date: date | None = None,
    *,
    max_picks: int | None = None,
    now: datetime | None = None,
) -> int:
    """Freeze the feed for feed_date (default: today UTC). Idempotent: if the
    date already has a feed, this is a no-op — the record never mutates.
    Returns the number of entries written (0 on re-runs)."""
    now = now or datetime.now(UTC)
    feed_date = feed_date or now.date()
    cap = max_picks if max_picks is not None else max_picks_from_env()

    with conn.cursor() as cur:
        cur.execute("select 1 from daily_feed where feed_date = %s limit 1", (feed_date,))
        if cur.fetchone() is not None:
            return 0  # frozen — append-only per date

    chosen = select_feed(_candidates(conn, feed_date, now), cap)
    with conn.cursor() as cur:
        for rank, c in enumerate(chosen, start=1):
            cur.execute(
                """
                insert into daily_feed (feed_date, rank, pick_id, label)
                values (%s, %s, %s, %s)
                """,
                (feed_date, rank, c.pick_id, label_for(c.model_status)),
            )
    return len(chosen)


def main() -> None:
    with connect() as conn:
        n = build_daily_feed(conn)
        conn.commit()
    print(f"daily feed: {n} entries frozen")


if __name__ == "__main__":
    main()
