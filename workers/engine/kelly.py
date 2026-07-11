"""Kelly staking (CLAUDE.md §8).

Published stakes are ALWAYS fractional (quarter-Kelly by config) and hard-capped.
Never publish full Kelly.

Unit convention: 1 stake_unit == 1% of bankroll (bankroll_units == 100).
"""

from __future__ import annotations

from core.config import EngineConfig


def kelly_fraction(p: float, price: float) -> float:
    """f* = (p*o - 1) / (o - 1). Fraction of bankroll; <= 0 means no bet."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0, 1), got {p}")
    if price <= 1.0:
        raise ValueError(f"decimal odds must be > 1.0, got {price}")
    return (p * price - 1.0) / (price - 1.0)


def stake_units(p: float, price: float, cfg: EngineConfig) -> float:
    """Publishable stake in units: fractional Kelly, floored at 0, hard-capped
    at cfg.max_stake_units."""
    f_star = kelly_fraction(p, price)
    if f_star <= 0.0:
        return 0.0
    units = f_star * cfg.kelly_fraction * cfg.bankroll_units
    return min(units, cfg.max_stake_units)
