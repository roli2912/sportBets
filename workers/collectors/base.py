"""Provider-agnostic collection plumbing: polling cadence, persistence.

Polling cadence per event (CLAUDE.md §8):

    > 48h        1/day
    48-24h       4/day
    24-2h        hourly
    2h-15m       every 5 min
    final 15m    every 2 min (budget permitting)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

from core.db import get_event_id, get_or_create_event, insert_snapshots
from core.protocols import OddsCollector
from core.types import Fixture, OddsSnapshot

_CADENCE: list[tuple[timedelta, timedelta]] = [
    # (time-to-kickoff greater than, poll interval)
    (timedelta(hours=48), timedelta(days=1)),
    (timedelta(hours=24), timedelta(hours=6)),
    (timedelta(hours=2), timedelta(hours=1)),
    (timedelta(minutes=15), timedelta(minutes=5)),
    (timedelta(0), timedelta(minutes=2)),
]


def poll_interval(time_to_kickoff: timedelta) -> timedelta | None:
    """Interval until the next poll for an event, or None once kicked off
    (closing capture takes over after commence_time)."""
    if time_to_kickoff <= timedelta(0):
        return None
    for floor, interval in _CADENCE:
        if time_to_kickoff > floor:
            return interval
    return timedelta(minutes=2)


def due_for_poll(
    commence_time: datetime,
    last_polled_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    """Idempotent scheduling predicate: has enough time passed since the last
    poll given the event's current cadence band?"""
    now = now or datetime.now(UTC)
    interval = poll_interval(commence_time - now)
    if interval is None:
        return False
    return last_polled_at is None or now - last_polled_at >= interval


def persist_fixtures(conn: psycopg.Connection, fixtures: list[Fixture]) -> int:
    """Upsert skeleton events for a provider's fixtures. Safe to re-run.

    Team/competition links stay NULL until the entity resolver (Phase 1)
    fills them; snapshots accrue from day one regardless (CLAUDE.md §13)."""
    for f in fixtures:
        get_or_create_event(
            conn,
            provider=f.provider,
            provider_key=f.provider_key,
            sport_id=f.sport_id,
            commence_time=f.commence_time,
        )
    return len(fixtures)


def persist_snapshots(conn: psycopg.Connection, snapshots: list[OddsSnapshot]) -> int:
    """Append snapshots, resolving each provider event key to our event row.

    A snapshot for an unknown event blocks loudly (raised), never guessed:
    unresolved entities fail visibly, not wrongly (CLAUDE.md §7). In the
    normal flow fixtures from the same poll are persisted first, so this
    only fires on provider inconsistencies."""
    written = 0
    by_key: dict[tuple[str, str], list[OddsSnapshot]] = {}
    for s in snapshots:
        by_key.setdefault((s.provider, s.event_provider_key), []).append(s)
    for (provider, key), batch in by_key.items():
        event_id = get_event_id(conn, provider=provider, provider_key=key)
        if event_id is None:
            raise LookupError(
                f"no event for snapshot batch (provider={provider}, key={key}) — "
                "fixtures must be persisted before odds"
            )
        written += insert_snapshots(conn, event_id, batch)
    return written


def collect_once(
    conn: psycopg.Connection,
    collector: OddsCollector,
    since: datetime,
    until: datetime,
) -> tuple[int, int]:
    """One poll cycle for one provider: fixtures first, then their odds.
    Returns (fixtures_seen, snapshots_written)."""
    fixtures, snapshots = collector.poll(since, until)
    n_fix = persist_fixtures(conn, fixtures)
    n_snap = persist_snapshots(conn, snapshots)
    conn.commit()
    return n_fix, n_snap
