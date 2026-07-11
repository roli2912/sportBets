"""Integration test for closing-line capture (CLAUDE.md §8)."""

from datetime import UTC, datetime, timedelta

from core.db import insert_snapshots, mark_closing_lines
from core.types import OddsSnapshot
from tests.conftest import requires_db

pytestmark = requires_db


def _snap(key: str, price: float, minutes_before_now: int, line: float | None = None):
    return OddsSnapshot(
        provider="test",
        event_provider_key=key,
        bookmaker="pinnacle",
        market="h2h",
        outcome="home",
        price=price,
        line=line,
        captured_at=datetime.now(UTC) - timedelta(minutes=minutes_before_now),
    )


def test_last_pre_kickoff_snapshot_marked_closing(conn, event_id):
    # event kicked off 60 min ago; snapshots at T-120, T-90, T-70, and one post-kickoff
    insert_snapshots(
        conn,
        event_id,
        [
            _snap("k", 2.20, 120),
            _snap("k", 2.10, 90),
            _snap("k", 2.05, 70),
            _snap("k", 1.80, 30),  # in-play price — must never be closing
        ],
    )
    marked = mark_closing_lines(conn, event_id)
    assert marked == 1

    with conn.cursor() as cur:
        cur.execute(
            "select price from odds_snapshots where event_id = %s and is_closing",
            (event_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert float(rows[0][0]) == 2.05


def test_closing_is_per_bookmaker_market_outcome_line(conn, event_id):
    insert_snapshots(
        conn,
        event_id,
        [
            _snap("k", 1.90, 90, line=26.5),
            _snap("k", 1.95, 70, line=26.5),
            _snap("k", 1.90, 70, line=27.5),  # different line -> its own closing
        ],
    )
    assert mark_closing_lines(conn, event_id) == 2
    # idempotent: second run marks nothing new
    assert mark_closing_lines(conn, event_id) == 0
