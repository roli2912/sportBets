"""Integration: board -> shadow picks -> settlement + CLV -> explainer.

Covers the §2 pipeline invariants: idempotent publishing (re-run = no-op),
threshold + quarter-Kelly staking, grading against event_results with the
no-vig sharp close, and the explainer's validate-before-save behavior.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from agents.explainer import explain_pending, validate_rationale
from core.config import EngineConfig
from engine.picks import ensure_market_models, market_model_id, publish_from_board
from grading.grade import grade_picks, novig_close
from tests.conftest import requires_db

pytestmark = requires_db

CFG = EngineConfig()  # defaults: EV>=2.5% after 0.5% haircut, quarter-Kelly


@pytest.fixture
def future_event(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into events (sport_id, commence_time, provider_keys)
            values ('cs2', %s, %s::jsonb) returning id
            """,
            (datetime.now(UTC) + timedelta(hours=2), f'{{"test": "{uuid.uuid4()}"}}'),
        )
        return cur.fetchone()[0]


def _board_row(conn, event_id, *, ev=0.06, price=2.20, p_true=0.4815, bookmaker="superbet.ro"):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into ev_board
              (event_id, bookmaker, market, outcome, line, price,
               sharp_price, p_true, novig_price, ev, captured_at)
            values (%s, %s, 'h2h', 'home', null, %s, 2.05, %s, %s, %s, now())
            """,
            (event_id, bookmaker, price, p_true, 1.0 / p_true, ev),
        )


@pytest.fixture
def clean_board(conn):
    with conn.cursor() as cur:
        cur.execute("truncate ev_board")  # rolled back by conftest


def test_publish_is_idempotent_and_thresholded(conn, clean_board, future_event):
    ensure_market_models(conn, CFG)
    _board_row(conn, future_event, ev=0.06)  # clears 2.5% + 0.5% haircut
    assert publish_from_board(conn, CFG) == 1
    assert publish_from_board(conn, CFG) == 0  # re-run is a no-op

    with conn.cursor() as cur:
        cur.execute(
            "select model_id, stake_units, features_hash from picks where event_id = %s",
            (future_event,),
        )
        model_id, stake, features_hash = cur.fetchone()
        assert model_id == market_model_id("cs2")
        # quarter-Kelly: f* = (0.4815*2.2 - 1)/1.2 ≈ 0.04944 -> *0.25*100, cap 2.0
        assert float(stake) == pytest.approx(1.2361, abs=1e-3)
        assert len(features_hash) == 64
        # full payload stored for §10 audits / §11 explainer
        cur.execute(
            "select count(*) from pick_features pf join picks p on p.id = pf.pick_id"
            " where p.event_id = %s",
            (future_event,),
        )
        assert cur.fetchone()[0] == 1


def test_publish_skips_below_threshold(conn, clean_board, future_event):
    ensure_market_models(conn, CFG)
    _board_row(conn, future_event, ev=0.02)  # 2% - 0.5% haircut < 2.5%
    assert publish_from_board(conn, CFG) == 0


def test_grade_with_novig_close_and_clv(conn, clean_board, future_event):
    ensure_market_models(conn, CFG)
    _board_row(conn, future_event, ev=0.06, price=2.20)
    assert publish_from_board(conn, CFG) == 1

    with conn.cursor() as cur:
        # closing sharp market: 1.90/1.90 -> no-vig 2.00 each side
        for outcome in ("home", "away"):
            cur.execute(
                """
                insert into odds_snapshots
                  (event_id, bookmaker, market, outcome, price, captured_at, is_closing)
                values (%s, 'pinnacle', 'h2h', %s, 1.90, now(), true)
                """,
                (future_event, outcome),
            )
        cur.execute(
            """
            insert into event_results (event_id, provider, home_score, away_score)
            values (%s, 'test', 2, 0)
            """,
            (future_event,),
        )

    assert novig_close(conn, future_event, "h2h", "home", None) == pytest.approx(2.0)
    assert grade_picks(conn) == 1
    assert grade_picks(conn) == 0  # already settled -> no-op

    with conn.cursor() as cur:
        cur.execute(
            """
            select s.result, s.closing_price, s.clv, s.pnl_units
            from settlements s join picks p on p.id = s.pick_id
            where p.event_id = %s
            """,
            (future_event,),
        )
        result, close, clv, pnl = cur.fetchone()
        assert result == "win"
        assert float(close) == pytest.approx(2.0)
        assert float(clv) == pytest.approx(2.20 / 2.0 - 1.0)  # +10%
        assert float(pnl) == pytest.approx(1.2361 * 1.20, abs=1e-3)


def test_grade_void_on_cancelled_event(conn, clean_board, future_event):
    ensure_market_models(conn, CFG)
    _board_row(conn, future_event, ev=0.06)
    publish_from_board(conn, CFG)
    with conn.cursor() as cur:
        cur.execute("update events set status = 'cancelled' where id = %s", (future_event,))
    assert grade_picks(conn) == 1
    with conn.cursor() as cur:
        cur.execute(
            "select s.result, s.pnl_units from settlements s"
            " join picks p on p.id = s.pick_id where p.event_id = %s",
            (future_event,),
        )
        result, pnl = cur.fetchone()
        assert result == "void"
        assert float(pnl) == 0.0


# --- explainer ---------------------------------------------------------------


def test_validate_rationale_rules():
    payload = {"price": 2.2, "ev": 0.043, "p_true": 0.4815}
    assert validate_rationale("Priced 2.2 vs a 4.3% edge.", payload)
    assert not validate_rationale("A guaranteed winner at 2.2.", payload)  # banned
    assert not validate_rationale("The model projects 3.7 goals.", payload)  # invented


def test_validate_rationale_allows_digit_bearing_names():
    """'bet365'/'G2' are names from the payload, not invented numbers."""
    payload = {"price": 1.674, "bookmaker": "bet365", "match": "Vitality vs G2 Esports"}
    assert validate_rationale("Bet365 offers 1.674 on Vitality against G2.", payload)
    assert not validate_rationale("bet365 offers 1.674; expect 27 rounds.", payload)


def test_explainer_stores_only_validated_text(conn, clean_board, future_event):
    ensure_market_models(conn, CFG)
    _board_row(conn, future_event, ev=0.06, price=2.20)
    publish_from_board(conn, CFG)

    # invalid first: invented number -> rejected, rationale stays NULL
    assert explain_pending(conn, llm=lambda s, u: "Expect 7.5 goals here.") == 0
    with conn.cursor() as cur:
        cur.execute("select rationale from picks where event_id = %s", (future_event,))
        assert cur.fetchone()[0] is None

    # valid: numbers all from the payload
    text = "Soft book offers 2.2 against a no-vig 2.05 sharp line, a 6% edge."
    assert explain_pending(conn, llm=lambda s, u: text) == 1
    with conn.cursor() as cur:
        cur.execute("select rationale from picks where event_id = %s", (future_event,))
        assert cur.fetchone()[0] == text
