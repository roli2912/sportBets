"""Grader: settle picks against ingested results and compute CLV (CLAUDE.md §8).

For every ungraded pick whose event has a result (or was cancelled):
- result + pnl via the pure rules in grading.settle (cancelled -> void),
- closing_price = the no-vig sharp close for the picked outcome, from the
  is_closing Pinnacle snapshots of that market/line, de-vigged,
- clv = price_at_publish / novig_close - 1.

If no sharp close was captured, the pick still settles (P&L is real money);
clv stays NULL and simply doesn't enter the §9 gate statistics. settlements is
INSERT-only in usage: already-graded picks are skipped, never re-graded.

Run standalone: uv run python -m grading.grade
"""

from __future__ import annotations

import sys
from uuid import UUID

import psycopg

from core.db import connect
from engine.board import SHARP_BOOKMAKER
from engine.devig import devig_multiplicative
from grading.clv import clv as clv_decimal
from grading.settle import settle_pick


def novig_close(
    conn: psycopg.Connection,
    event_id: UUID,
    market: str,
    outcome: str,
    line: float | None,
) -> float | None:
    """No-vig sharp closing price for one outcome, or None if the sharp book's
    closing market wasn't captured (or is incomplete / can't be de-vigged)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select outcome, price from odds_snapshots
            where event_id = %s and bookmaker = %s and market = %s
              and is_closing
              and coalesce(line, 'NaN'::numeric) = coalesce(%s, 'NaN'::numeric)
            """,
            (event_id, SHARP_BOOKMAKER, market, line),
        )
        prices = {r[0]: float(r[1]) for r in cur.fetchall()}
    if outcome not in prices or len(prices) < 2:
        return None
    probs = devig_multiplicative(prices)
    return 1.0 / probs[outcome]


def grade_picks(conn: psycopg.Connection) -> int:
    """Settle every gradable, ungraded pick. Returns settlements written."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select p.id, p.event_id, p.market, p.outcome, p.line,
                   p.price_at_publish, p.stake_units,
                   r.home_score, r.away_score, e.status
            from picks p
            join events e on e.id = p.event_id
            left join event_results r on r.event_id = p.event_id
            where not exists (select 1 from settlements s where s.pick_id = p.id)
              and (r.event_id is not null or e.status = 'cancelled')
            """
        )
        rows = cur.fetchall()

    graded = 0
    with conn.cursor() as cur:
        for pick_id, event_id, market, outcome, line, price, stake, hs, aw, status in rows:
            line_f = float(line) if line is not None else None
            price_f, stake_f = float(price), float(stake)
            if status == "cancelled" or hs is None:
                result, pnl = "void", 0.0
            else:
                try:
                    result, pnl = settle_pick(
                        market, outcome, line_f, int(hs), int(aw), stake_f, price_f
                    )
                except ValueError as exc:
                    # Unknown vocabulary: leave the pick open and visible, never
                    # guess a grade (§7 spirit).
                    print(f"grader: pick {pick_id} ungradable: {exc}", file=sys.stderr)
                    continue
            close = novig_close(conn, event_id, market, outcome, line_f)
            cur.execute(
                """
                insert into settlements (pick_id, result, closing_price, clv, pnl_units)
                values (%s, %s, %s, %s, %s)
                on conflict (pick_id) do nothing
                """,
                (
                    pick_id,
                    result,
                    close,
                    clv_decimal(price_f, close) if close is not None else None,
                    round(pnl, 4),
                ),
            )
            graded += cur.rowcount
    return graded


def main() -> None:
    with connect() as conn:
        n = grade_picks(conn)
        conn.commit()
    print(f"grader: {n} picks settled")


if __name__ == "__main__":
    main()
