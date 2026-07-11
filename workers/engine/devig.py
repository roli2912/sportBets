"""De-vig: turn a bookmaker's priced market into no-vig probabilities.

Primary sharp reference: Pinnacle (via TheRundown); fallback: median of the 3
sharpest available books, each de-vigged first (CLAUDE.md §8).

Start with the multiplicative method. Shin's method is a planned upgrade for
favourite–longshot-biased markets (football 1X2, outrights) — switching
requires an ADR and side-by-side comparability (§8).
"""

from __future__ import annotations

from collections.abc import Mapping
from statistics import median


def overround(odds: Mapping[str, float]) -> float:
    """Bookmaker margin: sum of implied probabilities minus 1."""
    _validate(odds)
    return sum(1.0 / o for o in odds.values()) - 1.0


def devig_multiplicative(odds: Mapping[str, float]) -> dict[str, float]:
    """p_i = (1/o_i) / sum_j(1/o_j). Probabilities sum to 1."""
    _validate(odds)
    implied = {k: 1.0 / o for k, o in odds.items()}
    total = sum(implied.values())
    return {k: v / total for k, v in implied.items()}


def novig_price(p: float) -> float:
    """Fair decimal price for a no-vig probability."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"probability must be in (0, 1), got {p}")
    return 1.0 / p


def consensus_probabilities(books: list[Mapping[str, float]]) -> dict[str, float]:
    """Fallback sharp reference: de-vig each book first, then take the median
    probability per outcome and renormalize (CLAUDE.md §8)."""
    if not books:
        raise ValueError("at least one book required")
    devigged = [devig_multiplicative(b) for b in books]
    outcomes = set(devigged[0])
    for d in devigged[1:]:
        if set(d) != outcomes:
            raise ValueError("all books must quote the same outcomes")
    medians = {k: median(d[k] for d in devigged) for k in outcomes}
    total = sum(medians.values())
    return {k: v / total for k, v in medians.items()}


def _validate(odds: Mapping[str, float]) -> None:
    if len(odds) < 2:
        raise ValueError("a market needs at least two outcomes to de-vig")
    for k, o in odds.items():
        if o <= 1.0:
            raise ValueError(f"decimal odds must be > 1.0 ({k}={o})")
