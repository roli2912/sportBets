"""Telegram publisher idempotency + append-only proof export (§13 Task 4)."""

import json
import uuid
from datetime import UTC, datetime, timedelta

from publishing.telegram import post_daily_digest, post_new_picks
from publishing.templates import COMPLIANCE_LINE, STATUS_LABELS
from tests.conftest import requires_db
from tools.proof_export import export_proof

# ------------------------------------------------------------------ seeding


def _seed_pick(conn, model_id, *, hours_ahead=3, price=2.10):
    """Future event + one published pick for it. Returns (event_id, pick_id)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into events (sport_id, commence_time, provider_keys)
            values ('cs2', %s, %s::jsonb) returning id
            """,
            (
                datetime.now(UTC) + timedelta(hours=hours_ahead),
                json.dumps({"test": str(uuid.uuid4())}),
            ),
        )
        event = cur.fetchone()[0]
        cur.execute(
            """
            insert into picks (model_id, event_id, market, outcome, price_at_publish,
                               bookmaker, stake_units, features_hash)
            values (%s, %s, 'h2h', 'home', %s, 'superbet.ro', 0.5, 'x')
            returning id
            """,
            (model_id, event, price),
        )
        return event, cur.fetchone()[0]


# ------------------------------------------------------------------ telegram


@requires_db
def test_pick_posts_once_with_compliance_line(conn, model_id) -> None:
    _seed_pick(conn, model_id)
    sent: list[str] = []
    assert post_new_picks(conn, sent.append) == 1
    msg = sent[0]
    assert COMPLIANCE_LINE in msg
    # research model -> the unverified label; the verified badge never leaks
    assert STATUS_LABELS["shadow"] in msg
    assert STATUS_LABELS["public"] not in msg
    assert "2.10 @ superbet.ro" in msg
    # idempotent: nothing new to post
    assert post_new_picks(conn, sent.append) == 0
    assert len(sent) == 1


@requires_db
def test_started_events_are_never_posted(conn, model_id) -> None:
    _seed_pick(conn, model_id, hours_ahead=-1)  # kickoff already passed
    sent: list[str] = []
    assert post_new_picks(conn, sent.append) == 0
    assert sent == []


@requires_db
def test_digest_posts_once_and_only_when_frozen(conn, model_id) -> None:
    sent: list[str] = []
    feed_date = (datetime.now(UTC) + timedelta(days=200)).date()  # isolated date
    # nothing frozen for that date -> no digest, nothing recorded
    assert post_daily_digest(conn, sent.append, feed_date) is False
    assert sent == []

    _, pick = _seed_pick(conn, model_id)
    with conn.cursor() as cur:
        cur.execute(
            "insert into daily_feed (feed_date, rank, pick_id, label) values (%s, 1, %s, %s)",
            (feed_date, pick, "market_edge"),
        )
    assert post_daily_digest(conn, sent.append, feed_date) is True
    assert len(sent) == 1
    assert COMPLIANCE_LINE in sent[0]
    assert STATUS_LABELS["shadow"] in sent[0]
    assert str(feed_date) in sent[0]
    # frozen date already delivered -> no repeat
    assert post_daily_digest(conn, sent.append, feed_date) is False
    assert len(sent) == 1


# --------------------------------------------------------------- proof export


@requires_db
def test_proof_export_is_append_only_and_idempotent(conn, model_id, tmp_path) -> None:
    _, pick = _seed_pick(conn, model_id)
    counts = export_proof(conn, tmp_path)
    assert counts["picks"] >= 1

    picks_file = tmp_path / "picks.jsonl"
    first_bytes = picks_file.read_bytes()
    lines = [json.loads(x) for x in picks_file.read_text().splitlines()]
    assert any(row["pick_id"] == str(pick) for row in lines)
    assert all("price_at_publish" in row and "published_at" in row for row in lines)

    # re-run: appends nothing, bytes identical (append-only proof)
    assert export_proof(conn, tmp_path) == {"picks": 0, "settlements": 0}
    assert picks_file.read_bytes() == first_bytes

    # a settlement arriving later appends to settlements.jsonl only
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into settlements (pick_id, result, closing_price, clv, pnl_units)
            values (%s, 'win', 2.00, 0.05, 0.55)
            """,
            (pick,),
        )
    counts = export_proof(conn, tmp_path)
    assert counts["settlements"] >= 1
    assert counts["picks"] == 0
    assert picks_file.read_bytes() == first_bytes  # existing file untouched
    srow = [
        json.loads(x)
        for x in (tmp_path / "settlements.jsonl").read_text().splitlines()
        if json.loads(x)["pick_id"] == str(pick)
    ]
    assert srow and srow[0]["result"] == "win" and srow[0]["clv"] == 0.05
