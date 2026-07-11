"""Postgres access helpers (psycopg 3).

Workers are idempotent: every write here is an upsert keyed on natural keys or
an append to an append-only table (CLAUDE.md §14). UTC everywhere.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

import psycopg

from core.config import database_url
from core.types import OddsSnapshot


def connect(dsn: str | None = None) -> psycopg.Connection:
    return psycopg.connect(dsn or database_url())


def get_event_id(conn: psycopg.Connection, *, provider: str, provider_key: str) -> UUID | None:
    with conn.cursor() as cur:
        cur.execute(
            "select id from events where provider_keys @> %s::jsonb",
            (json.dumps({provider: provider_key}),),
        )
        row = cur.fetchone()
        return row[0] if row else None


def get_or_create_event(
    conn: psycopg.Connection,
    *,
    provider: str,
    provider_key: str,
    sport_id: str,
    commence_time: datetime,
    home_name_raw: str | None = None,
    away_name_raw: str | None = None,
) -> UUID:
    """Find an event by this provider's key, or create a skeleton row.

    Raw provider team names are stored so the entity resolver (§7) can link
    canonical team ids later; snapshots must accrue from day one regardless
    (CLAUDE.md §13). Cross-provider event merging is the resolver's job, not
    the collector's.
    """
    key_obj = json.dumps({provider: provider_key})
    with conn.cursor() as cur:
        cur.execute(
            "select id from events where provider_keys @> %s::jsonb",
            (key_obj,),
        )
        row = cur.fetchone()
        if row:
            # Backfill raw names on rows created before this column existed.
            cur.execute(
                """
                update events
                set home_name_raw = coalesce(home_name_raw, %s),
                    away_name_raw = coalesce(away_name_raw, %s)
                where id = %s
                """,
                (home_name_raw, away_name_raw, row[0]),
            )
            return row[0]
        cur.execute(
            """
            insert into events
              (sport_id, commence_time, provider_keys, home_name_raw, away_name_raw)
            values (%s, %s, %s::jsonb, %s, %s)
            returning id
            """,
            (sport_id, commence_time.astimezone(UTC), key_obj, home_name_raw, away_name_raw),
        )
        return cur.fetchone()[0]  # type: ignore[index]


def insert_snapshots(
    conn: psycopg.Connection,
    event_id: UUID,
    snapshots: Iterable[OddsSnapshot],
) -> int:
    """Append odds snapshots for a resolved event. Ensures the monthly
    partition exists before writing."""
    rows = [
        (
            event_id,
            s.bookmaker,
            s.market,
            s.outcome,
            s.price,
            s.line,
            s.captured_at.astimezone(UTC),
        )
        for s in snapshots
    ]
    if not rows:
        return 0
    with conn.cursor() as cur:
        months = {r[6].date().replace(day=1) for r in rows}
        for month in months:
            cur.execute("select ensure_odds_snapshots_partition(%s)", (month,))
        cur.executemany(
            """
            insert into odds_snapshots
              (event_id, bookmaker, market, outcome, price, line, captured_at)
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
    return len(rows)


def get_last_poll(conn: psycopg.Connection, provider: str) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute(
            "select last_polled_at from collector_runs where provider = %s",
            (provider,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def set_last_poll(conn: psycopg.Connection, provider: str, polled_at: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into collector_runs (provider, last_polled_at)
            values (%s, %s)
            on conflict (provider) do update set last_polled_at = excluded.last_polled_at
            """,
            (provider, polled_at.astimezone(UTC)),
        )


def next_provider_kickoff(
    conn: psycopg.Connection, provider: str, now: datetime
) -> datetime | None:
    """Earliest upcoming commence_time among events known to this provider —
    drives the §8 cadence band for the provider's next poll."""
    with conn.cursor() as cur:
        cur.execute(
            "select min(commence_time) from events where provider_keys ? %s and commence_time > %s",
            (provider, now.astimezone(UTC)),
        )
        row = cur.fetchone()
        return row[0] if row else None


def mark_closing_lines(conn: psycopg.Connection, event_id: UUID) -> int:
    """Mark last pre-kickoff snapshot per (bookmaker, market, outcome, line)
    as closing. Delegates to the SQL function so the logic has one home."""
    with conn.cursor() as cur:
        cur.execute("select mark_closing_lines(%s)", (event_id,))
        return int(cur.fetchone()[0])  # type: ignore[index]


def events_awaiting_closing_capture(conn: psycopg.Connection) -> list[UUID]:
    """Events past commence_time that still have un-flagged snapshots."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select distinct e.id
            from events e
            join odds_snapshots o on o.event_id = e.id
            where e.commence_time <= now()
              and not exists (
                select 1 from odds_snapshots c
                where c.event_id = e.id and c.is_closing
              )
            """
        )
        return [r[0] for r in cur.fetchall()]
