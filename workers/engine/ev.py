"""Expected value against a de-vigged true probability (CLAUDE.md §8)."""

from __future__ import annotations

from core.config import EngineConfig


def expected_value(p_true: float, price_offered: float) -> float:
    """EV = p_true * o_offered - 1 (decimal odds)."""
    if not 0.0 < p_true < 1.0:
        raise ValueError(f"p_true must be in (0, 1), got {p_true}")
    if price_offered <= 1.0:
        raise ValueError(f"decimal odds must be > 1.0, got {price_offered}")
    return p_true * price_offered - 1.0


def publishable_ev(p_true: float, price_offered: float, cfg: EngineConfig) -> float | None:
    """EV after the margin haircut, or None if below the Layer-1 publish
    threshold. Thresholds come from config, not code."""
    ev = expected_value(p_true, price_offered) - cfg.margin_haircut
    return ev if ev >= cfg.ev_threshold else None
