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
from core.types import OddsSnapshot, Result


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


def _oriented_scores(cur: psycopg.Cursor, event_id: UUID, r: Result) -> tuple[int, int] | None:
    """Align a name-carrying result with the event's home/away orientation.

    Esports providers order teams arbitrarily; after cross-provider merges the
    event's orientation can differ from the result's, and writing scores by
    position would grade wins as losses. Names resolve through team_aliases
    (the resolver's output). Anything unresolved -> None: skip now, retry on a
    later poll once the resolver has caught up — never guess (§7)."""
    cur.execute("select home_team, away_team from events where id = %s", (event_id,))
    row = cur.fetchone()
    if row is None or row[0] is None or row[1] is None:
        return None  # event teams not resolved yet
    event_home, event_away = row
    ids: list[UUID | None] = []
    for name in (r.home_name, r.away_name):
        cur.execute(
            "select team_id from team_aliases where provider = %s and lower(alias) = lower(%s)",
            (r.provider, name),
        )
        alias = cur.fetchone()
        ids.append(alias[0] if alias else None)
    if (ids[0], ids[1]) == (event_home, event_away):
        return r.home_score, r.away_score
    if (ids[0], ids[1]) == (event_away, event_home):
        return r.away_score, r.home_score
    return None  # unresolved alias or team mismatch — block visibly, not wrongly


def persist_results(conn: psycopg.Connection, results: Iterable[Result]) -> int:
    """Attach provider results to known events and mark them finished.

    First provider to report wins — event_results rows never mutate (they feed
    grading, §10.4). Results for events we never ingested are skipped silently:
    they belong to fixtures outside our window, not to the pick universe.
    Results carrying team names are aligned to the event's resolved orientation
    first (see _oriented_scores); positional trust is football-only.
    """
    written = 0
    with conn.cursor() as cur:
        for r in results:
            event_id = get_event_id(conn, provider=r.provider, provider_key=r.provider_key)
            if event_id is None:
                continue
            if r.home_name and r.away_name:
                scores = _oriented_scores(cur, event_id, r)
                if scores is None:
                    continue
            else:
                scores = (r.home_score, r.away_score)
            cur.execute(
                """
                insert into event_results
                  (event_id, provider, home_score, away_score, finished_at)
                values (%s, %s, %s, %s, %s)
                on conflict (event_id) do nothing
                """,
                (event_id, r.provider, scores[0], scores[1], r.finished_at),
            )
            if cur.rowcount:
                cur.execute(
                    "update events set status = 'finished' where id = %s",
                    (event_id,),
                )
                written += 1
    return written


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
