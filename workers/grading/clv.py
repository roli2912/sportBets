"""Closing-line value — the primary model KPI (CLAUDE.md §8).

Win rate is a vanity metric at small N; CLV against the no-vig sharp close is
what gets reported and gated on (§9).
"""

from __future__ import annotations


def clv(price_at_publish: float, novig_closing_price: float) -> float:
    """clv = price_at_publish / novig_closing_price - 1.

    Stored as a decimal (0.025 == +2.5%); display layer formats as %.
    """
    if price_at_publish <= 1.0:
        raise ValueError(f"decimal odds must be > 1.0, got {price_at_publish}")
    if novig_closing_price <= 1.0:
        raise ValueError(f"no-vig close must be > 1.0, got {novig_closing_price}")
    return price_at_publish / novig_closing_price - 1.0


def probability_delta(price_at_publish: float, novig_closing_price: float) -> float:
    """Probability-space CLV: p_close - p_publish (positive = beat the close).
    Reported alongside price-space CLV in analytics (§8)."""
    if price_at_publish <= 1.0 or novig_closing_price <= 1.0:
        raise ValueError("decimal odds must be > 1.0")
    return 1.0 / novig_closing_price - 1.0 / price_at_publish


def pnl_units(result: str, stake: float, price: float) -> float:
    """Flat settlement P&L in stake units for a graded pick."""
    match result:
        case "win":
            return stake * (price - 1.0)
        case "loss":
            return -stake
        case "push" | "void":
            return 0.0
        case _:
            raise ValueError(f"unknown result {result!r}")
