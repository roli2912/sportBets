"""Closing-line capture job (CLAUDE.md §8).

Run frequently (e.g. every 2 minutes via cron/systemd timer). For every event
past commence_time whose snapshots are not yet flagged, mark the last snapshot
per (bookmaker, market, outcome, line) as is_closing = true. Idempotent.
"""

from __future__ import annotations

import psycopg

from core.db import connect, events_awaiting_closing_capture, mark_closing_lines


def run_closing_capture(conn: psycopg.Connection) -> dict[str, int]:
    """Returns {event_id: rows_marked} for observability."""
    marked: dict[str, int] = {}
    for event_id in events_awaiting_closing_capture(conn):
        marked[str(event_id)] = mark_closing_lines(conn, event_id)
    conn.commit()
    return marked


def main() -> None:
    with connect() as conn:
        marked = run_closing_capture(conn)
    total = sum(marked.values())
    print(f"closing capture: {len(marked)} events, {total} snapshots flagged")


if __name__ == "__main__":
    main()
