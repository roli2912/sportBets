"""Pure settlement rules: (market, outcome, line, scores) -> result + P&L.

Sport-agnostic on purpose: "score" is goals for football, maps won for CS2 —
whatever the event_results row carries for that sport. Markets settle on the
scores stored there (for football that is the 90-minute score, see
docs/providers/api_football.md).

Asian quarter-lines (±0.25, ±0.75, ...) are graded as two half-stakes on the
adjacent half-lines (industry standard). settlements.result stores the sign of
the net P&L in that case; pnl_units carries the exact number.
"""

from __future__ import annotations

from grading.clv import pnl_units

_HANDICAP_MARKETS = frozenset({"asian_handicap", "map_handicap"})
_TOTALS_MARKETS = frozenset({"totals", "total_maps"})


def settle_simple(
    market: str,
    outcome: str,
    line: float | None,
    home_score: int,
    away_score: int,
) -> str:
    """win/loss/push for whole/half lines. Raises on unknown vocabulary —
    an ungradable pick must fail loudly, never guess (§7 spirit)."""
    if market == "h2h":
        winner = (
            "home" if home_score > away_score else "away" if away_score > home_score else "draw"
        )
        return "win" if outcome == winner else "loss"

    if market in _TOTALS_MARKETS:
        if line is None:
            raise ValueError(f"{market} pick has no line")
        total = home_score + away_score
        if total == line:
            return "push"
        went_over = total > line
        if outcome == "over":
            return "win" if went_over else "loss"
        if outcome == "under":
            return "loss" if went_over else "win"
        raise ValueError(f"unknown totals outcome {outcome!r}")

    if market in _HANDICAP_MARKETS:
        if line is None:
            raise ValueError(f"{market} pick has no line")
        if outcome == "home":
            adj = (home_score - away_score) + line
        elif outcome == "away":
            adj = (away_score - home_score) + line
        else:
            raise ValueError(f"unknown handicap outcome {outcome!r}")
        if adj > 0:
            return "win"
        if adj < 0:
            return "loss"
        return "push"

    if market == "btts":
        both = home_score > 0 and away_score > 0
        if outcome == "yes":
            return "win" if both else "loss"
        if outcome == "no":
            return "loss" if both else "win"
        raise ValueError(f"unknown btts outcome {outcome!r}")

    raise ValueError(f"no settlement rule for market {market!r}")


def _is_quarter_line(line: float) -> bool:
    return (line * 4) % 2 != 0  # ±0.25, ±0.75, ... (x4 -> odd integer)


def settle_pick(
    market: str,
    outcome: str,
    line: float | None,
    home_score: int,
    away_score: int,
    stake: float,
    price: float,
) -> tuple[str, float]:
    """(result, pnl_units) for one pick. Quarter handicap lines split into two
    half-stakes on line ± 0.25; the reported result is the sign of net P&L."""
    if market in _HANDICAP_MARKETS and line is not None and _is_quarter_line(line):
        pnl = 0.0
        for half in (line - 0.25, line + 0.25):
            r = settle_simple(market, outcome, half, home_score, away_score)
            pnl += pnl_units(r, stake / 2.0, price)
        eps = 1e-9
        result = "win" if pnl > eps else "loss" if pnl < -eps else "push"
        return result, pnl

    result = settle_simple(market, outcome, line, home_score, away_score)
    return result, pnl_units(result, stake, price)
