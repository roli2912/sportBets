"""Outbound message templates must always carry the §2.5 compliance line."""

from datetime import UTC, datetime

from agents.explainer import BANNED_STRINGS
from publishing.templates import COMPLIANCE_LINE, format_daily_digest, format_pick


def _pick(**overrides) -> str:
    defaults = dict(
        match="Natus Vincere – FaZe Clan",
        market="h2h",
        outcome="home",
        price=2.05,
        bookmaker="superbet.ro",
        published_at=datetime(2026, 7, 12, 9, 0, tzinfo=UTC),
        model_status="shadow",
    )
    defaults.update(overrides)
    return format_pick(**defaults)


def test_every_pick_message_carries_compliance_line() -> None:
    assert COMPLIANCE_LINE in _pick()
    assert COMPLIANCE_LINE in _pick(model_status="public", rationale="Edge vs no-vig close.")
    assert "18+" in COMPLIANCE_LINE


def test_status_labels_never_blur() -> None:
    assert "VERIFIED" in _pick(model_status="public")
    assert "VERIFIED" not in _pick(model_status="shadow")
    assert "validation ongoing" in _pick(model_status="shadow")
    # unknown statuses degrade to the humbler label, never to "verified"
    assert "VERIFIED" not in _pick(model_status="research")


def test_digest_carries_compliance_line_even_when_empty() -> None:
    empty = format_daily_digest("2026-07-12", [])
    assert COMPLIANCE_LINE in empty
    full = format_daily_digest("2026-07-12", [_pick()])
    assert COMPLIANCE_LINE in full


def test_templates_emit_no_banned_strings() -> None:
    msg = (_pick(model_status="public") + format_daily_digest("2026-07-12", [])).lower()
    for banned in BANNED_STRINGS:
        assert banned not in msg


def test_line_market_formatting() -> None:
    msg = _pick(market="total_maps", outcome="over", line=2.5)
    assert "total_maps 2.5 — over" in msg
