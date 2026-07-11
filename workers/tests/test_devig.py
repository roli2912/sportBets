import math

import pytest

from engine.devig import consensus_probabilities, devig_multiplicative, novig_price, overround


def test_multiplicative_probabilities_sum_to_one():
    probs = devig_multiplicative({"home": 1.91, "away": 1.91})
    assert math.isclose(sum(probs.values()), 1.0, rel_tol=1e-12)
    assert math.isclose(probs["home"], 0.5)


def test_multiplicative_three_way():
    odds = {"home": 2.45, "draw": 3.30, "away": 3.05}
    probs = devig_multiplicative(odds)
    assert math.isclose(sum(probs.values()), 1.0, rel_tol=1e-12)
    # ordering preserved: shorter price -> higher probability
    assert probs["home"] > probs["away"] > probs["draw"]


def test_overround_positive_for_vigged_market():
    assert overround({"home": 1.91, "away": 1.91}) == pytest.approx(2 / 1.91 - 1)


def test_novig_price_roundtrip():
    probs = devig_multiplicative({"over": 1.87, "under": 1.95})
    assert novig_price(probs["over"]) == pytest.approx(1 / probs["over"])


def test_consensus_median_of_devigged_books():
    books = [
        {"home": 1.90, "away": 1.90},
        {"home": 1.85, "away": 1.95},
        {"home": 1.95, "away": 1.85},
    ]
    probs = consensus_probabilities(books)
    assert math.isclose(sum(probs.values()), 1.0, rel_tol=1e-12)
    assert probs["home"] == pytest.approx(0.5)


def test_rejects_invalid_odds():
    with pytest.raises(ValueError):
        devig_multiplicative({"home": 1.0, "away": 2.0})
    with pytest.raises(ValueError):
        devig_multiplicative({"home": 1.9})
    with pytest.raises(ValueError):
        consensus_probabilities([{"home": 1.9, "away": 1.9}, {"home": 1.9, "draw": 1.9}])
