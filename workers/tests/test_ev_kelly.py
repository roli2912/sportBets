import pytest

from core.config import EngineConfig
from engine.ev import expected_value, publishable_ev
from engine.kelly import kelly_fraction, stake_units
from grading.clv import clv, pnl_units, probability_delta

CFG = EngineConfig()  # defaults: 2.5% threshold, 0.5% haircut, quarter-Kelly, cap 2.0


def test_expected_value():
    assert expected_value(0.55, 2.00) == pytest.approx(0.10)
    assert expected_value(0.50, 1.90) == pytest.approx(-0.05)


def test_publishable_ev_threshold_and_haircut():
    # raw EV 10%, haircut 0.5% -> 9.5%, above 2.5% threshold
    assert publishable_ev(0.55, 2.00, CFG) == pytest.approx(0.095)
    # raw EV 2.9%, haircut -> 2.4%, below threshold -> None
    assert publishable_ev(0.5145, 2.00, CFG) is None


def test_kelly_fraction():
    # p=0.55, o=2.0 -> f* = (1.10 - 1) / 1 = 0.10
    assert kelly_fraction(0.55, 2.00) == pytest.approx(0.10)
    # negative edge -> negative fraction
    assert kelly_fraction(0.45, 2.00) < 0


def test_stake_units_quarter_kelly_and_cap():
    # f*=0.10 -> quarter = 0.025 of bankroll -> 2.5 units -> capped at 2.0
    assert stake_units(0.55, 2.00, CFG) == pytest.approx(2.0)
    # smaller edge stays under the cap: p=0.52, o=2.0 -> f*=0.04 -> 1.0 unit
    assert stake_units(0.52, 2.00, CFG) == pytest.approx(1.0)
    # no edge -> zero stake
    assert stake_units(0.45, 2.00, CFG) == 0.0


def test_never_full_kelly_config():
    assert CFG.kelly_fraction <= 0.25
    assert CFG.max_stake_units == 2.0


def test_clv():
    # published 2.10, no-vig close 2.00 -> +5%
    assert clv(2.10, 2.00) == pytest.approx(0.05)
    assert clv(1.90, 2.00) == pytest.approx(-0.05)
    assert probability_delta(2.10, 2.00) == pytest.approx(1 / 2.0 - 1 / 2.1)


def test_pnl_units():
    assert pnl_units("win", 2.0, 2.10) == pytest.approx(2.2)
    assert pnl_units("loss", 2.0, 2.10) == pytest.approx(-2.0)
    assert pnl_units("push", 2.0, 2.10) == 0.0
    assert pnl_units("void", 2.0, 2.10) == 0.0
    with pytest.raises(ValueError):
        pnl_units("cancelled", 1.0, 2.0)
