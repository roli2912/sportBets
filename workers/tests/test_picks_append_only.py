"""Proves the append-only guarantees on `picks` (CLAUDE.md §2.1, §6, §14).

Required by convention: any change touching picks/settlements must include a
test proving the guarantees still hold. Runs against a real Postgres with
migrations applied (CI provides one; locally set TEST_DATABASE_URL).
"""

import psycopg
import pytest

from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def pick_id(conn, event_id, model_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into picks
              (model_id, event_id, market, outcome, price_at_publish, bookmaker, stake_units)
            values (%s, %s, 'h2h', 'home', 2.10, 'pinnacle', 1.0)
            returning id
            """,
            (model_id, event_id),
        )
        return cur.fetchone()[0]


def test_picks_update_is_forbidden(conn, pick_id):
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with conn.cursor() as cur:
            cur.execute("update picks set price_at_publish = 9.99 where id = %s", (pick_id,))


def test_picks_delete_is_forbidden(conn, pick_id):
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with conn.cursor() as cur:
            cur.execute("delete from picks where id = %s", (pick_id,))


def test_append_only_trigger_exists(conn):
    """Belt-and-braces: the trigger itself must exist, not just behave."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select count(*) from pg_trigger
            where tgname = 'picks_append_only' and tgrelid = 'picks'::regclass
            """
        )
        assert cur.fetchone()[0] == 1


def test_rationale_backfill_allowed_once(conn, pick_id):
    """§11 exception: a NULL rationale may be backfilled exactly once; a
    non-null rationale is immutable like everything else."""
    with conn.cursor() as cur:
        cur.execute(
            "update picks set rationale = 'explainer text' where id = %s",
            (pick_id,),
        )
        assert cur.rowcount == 1
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            cur.execute(
                "update picks set rationale = 'rewritten' where id = %s",
                (pick_id,),
            )


def test_rationale_backfill_cannot_smuggle_other_changes(conn, pick_id):
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with conn.cursor() as cur:
            cur.execute(
                """
                update picks set rationale = 'text', price_at_publish = 9.99
                where id = %s
                """,
                (pick_id,),
            )


def test_settlement_insert_allowed_once(conn, pick_id):
    """Settlements append normally; a second settlement for the same pick is
    rejected by the primary key."""
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into settlements (pick_id, result, closing_price, clv, pnl_units)
            values (%s, 'win', 2.00, 0.05, 1.10)
            """,
            (pick_id,),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                """
                insert into settlements (pick_id, result, closing_price, clv, pnl_units)
                values (%s, 'loss', 2.00, 0.05, -1.0)
                """,
                (pick_id,),
            )
