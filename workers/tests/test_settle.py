"""Pure settlement rules (grading/settle.py). No DB required."""

import pytest

from grading.settle import settle_pick, settle_simple

# --- h2h -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "hs", "aw", "expected"),
    [
        ("home", 2, 1, "win"),
        ("home", 1, 1, "loss"),  # 1X2: draw loses a home pick
        ("home", 0, 1, "loss"),
        ("draw", 1, 1, "win"),
        ("draw", 2, 1, "loss"),
        ("away", 0, 3, "win"),
    ],
)
def test_h2h(outcome, hs, aw, expected):
    assert settle_simple("h2h", outcome, None, hs, aw) == expected


# --- totals ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("market", "outcome", "line", "hs", "aw", "expected"),
    [
        ("totals", "over", 2.5, 2, 1, "win"),
        ("totals", "over", 2.5, 1, 1, "loss"),
        ("totals", "under", 2.5, 1, 1, "win"),
        ("totals", "over", 2.0, 1, 1, "push"),  # whole line lands exactly
        ("total_maps", "over", 2.5, 2, 1, "win"),  # maps: 2-1 = 3 maps
        ("total_maps", "under", 2.5, 2, 0, "win"),
    ],
)
def test_totals(market, outcome, line, hs, aw, expected):
    assert settle_simple(market, outcome, line, hs, aw) == expected


# --- handicaps --------------------------------------------------------------


@pytest.mark.parametrize(
    ("market", "outcome", "line", "hs", "aw", "expected"),
    [
        ("asian_handicap", "home", -1.0, 2, 0, "win"),
        ("asian_handicap", "home", -1.0, 1, 0, "push"),
        ("asian_handicap", "home", -1.0, 1, 1, "loss"),
        ("asian_handicap", "away", 1.5, 1, 0, "win"),
        ("map_handicap", "home", -1.5, 2, 0, "win"),
        ("map_handicap", "away", 1.5, 2, 1, "win"),
    ],
)
def test_handicaps(market, outcome, line, hs, aw, expected):
    assert settle_simple(market, outcome, line, hs, aw) == expected


def test_quarter_line_half_win():
    """-0.25 home, won by 0 goals margin? No: home -0.25 with a 1-0 win is a
    full win; the interesting case is a draw -> half stake pushes (line 0),
    half loses (line -0.5) => net -stake/2."""
    result, pnl = settle_pick("asian_handicap", "home", -0.25, 1, 1, stake=1.0, price=1.95)
    assert result == "loss"
    assert pnl == pytest.approx(-0.5)


def test_quarter_line_half_win_positive():
    """home -0.75, wins by exactly 1: half (line -0.5) wins, half (line -1.0)
    pushes => pnl = stake/2 * (price-1)."""
    result, pnl = settle_pick("asian_handicap", "home", -0.75, 2, 1, stake=1.0, price=2.10)
    assert result == "win"
    assert pnl == pytest.approx(0.55)


# --- btts / unknowns ---------------------------------------------------------


def test_btts():
    assert settle_simple("btts", "yes", None, 1, 1) == "win"
    assert settle_simple("btts", "yes", None, 2, 0) == "loss"
    assert settle_simple("btts", "no", None, 0, 0) == "win"


def test_unknown_market_raises():
    with pytest.raises(ValueError, match="no settlement rule"):
        settle_simple("first_blood", "home", None, 1, 0)


def test_missing_line_raises():
    with pytest.raises(ValueError, match="no line"):
        settle_simple("totals", "over", None, 1, 0)


def test_settle_pick_pnl_plain():
    result, pnl = settle_pick("h2h", "home", None, 2, 0, stake=1.5, price=2.20)
    assert result == "win"
    assert pnl == pytest.approx(1.5 * 1.20)
