"""Daily Best Bets feed: ranking, correlated-market dedupe, label split,
per-date immutability (pivot ADR 0002; CLAUDE.md §13 Task 2)."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from engine.daily_feed import (
    LABEL_MARKET_EDGE,
    LABEL_VERIFIED,
    FeedCandidate,
    build_daily_feed,
    label_for,
    select_feed,
)
from tests.conftest import requires_db

# ---------------------------------------------------------------- pure logic


def _c(pick, event, status, ev):
    return FeedCandidate(pick_id=pick, event_id=event, model_status=status, ev=ev)


def test_label_split_never_blurs() -> None:
    assert label_for("public") == LABEL_VERIFIED
    # everything that has not passed the §9 gates is a market-edge signal
    for status in ("shadow", "research", "retired", "", "unknown"):
        assert label_for(status) == LABEL_MARKET_EDGE


def test_verified_ranks_above_any_market_edge() -> None:
    chosen = select_feed(
        [
            _c("p1", "e1", "shadow", ev=0.30),  # huge edge, unverified
            _c("p2", "e2", "public", ev=0.01),  # tiny edge, gate-passed
        ]
    )
    assert [c.pick_id for c in chosen] == ["p2", "p1"]


def test_within_label_ev_descending() -> None:
    chosen = select_feed(
        [
            _c("low", "e1", "shadow", ev=0.03),
            _c("high", "e2", "shadow", ev=0.08),
            _c("mid", "e3", "shadow", ev=0.05),
        ]
    )
    assert [c.pick_id for c in chosen] == ["high", "mid", "low"]


def test_correlated_markets_deduped_to_best_per_event() -> None:
    chosen = select_feed(
        [
            _c("h2h", "e1", "shadow", ev=0.06),
            _c("total", "e1", "shadow", ev=0.04),  # same event -> dropped
            _c("other", "e2", "shadow", ev=0.05),
        ]
    )
    assert [c.pick_id for c in chosen] == ["h2h", "other"]


def test_cap_applies_after_dedupe() -> None:
    cands = [_c(f"p{i}", f"e{i}", "shadow", ev=0.10 - i * 0.01) for i in range(10)]
    chosen = select_feed(cands, max_picks=5)
    assert len(chosen) == 5
    assert chosen[0].pick_id == "p0"


# ---------------------------------------------------------------- DB behavior


@requires_db
def test_build_freezes_once_and_rerun_is_noop(conn, model_id) -> None:
    now = datetime.now(UTC)
    commence = now + timedelta(hours=2)
    feed_date = commence.date()
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into events (sport_id, commence_time, provider_keys)
            values ('cs2', %s, %s::jsonb) returning id
            """,
            (commence, json.dumps({"test": str(uuid.uuid4())})),
        )
        event = cur.fetchone()[0]
        cur.execute("update models set status = 'shadow' where id = %s", (model_id,))
        cur.execute(
            """
            insert into picks (model_id, event_id, market, outcome, price_at_publish,
                               bookmaker, stake_units, features_hash)
            values (%s, %s, 'h2h', 'home', 2.10, 'superbet.ro', 0.5, 'x')
            returning id
            """,
            (model_id, event),
        )
        pick = cur.fetchone()[0]
        cur.execute(
            "insert into pick_features (pick_id, payload) values (%s, %s)",
            (pick, json.dumps({"ev": 0.041})),
        )

    assert build_daily_feed(conn, feed_date, now=now) == 1
    with conn.cursor() as cur:
        cur.execute(
            "select rank, pick_id, label from daily_feed where feed_date = %s", (feed_date,)
        )
        rows = cur.fetchall()
    assert rows == [(1, pick, LABEL_MARKET_EDGE)]  # shadow never labels verified

    # frozen: re-run writes nothing, even though the pick is still there
    assert build_daily_feed(conn, feed_date, now=now) == 0


@requires_db
def test_daily_feed_is_append_only(conn, model_id) -> None:
    now = datetime.now(UTC)
    commence = now + timedelta(hours=3)
    feed_date = commence.date()
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into events (sport_id, commence_time, provider_keys)
            values ('cs2', %s, %s::jsonb) returning id
            """,
            (commence, json.dumps({"test": str(uuid.uuid4())})),
        )
        event = cur.fetchone()[0]
        cur.execute(
            """
            insert into picks (model_id, event_id, market, outcome, price_at_publish,
                               bookmaker, stake_units, features_hash)
            values (%s, %s, 'h2h', 'away', 1.90, 'superbet.ro', 0.5, 'y')
            returning id
            """,
            (model_id, event),
        )
        pick = cur.fetchone()[0]
        cur.execute(
            "insert into daily_feed (feed_date, rank, pick_id, label) values (%s, 1, %s, %s)",
            (feed_date, pick, LABEL_MARKET_EDGE),
        )
        cur.execute("savepoint sp")
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            cur.execute(
                "update daily_feed set label = 'verified' where feed_date = %s", (feed_date,)
            )
        cur.execute("rollback to savepoint sp")
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            cur.execute("delete from daily_feed where pick_id = %s", (pick,))
        cur.execute("rollback to savepoint sp")
