"""+EV board refresh (Layer 1, CLAUDE.md §8).

For every upcoming event: take the latest snapshot per (bookmaker, market,
outcome, line), de-vig the sharp reference (Pinnacle), and compute EV for every
soft-book price against those no-vig probabilities. Results land in the
`ev_board` cache table, rebuilt wholesale each run (idempotent).

Run: uv run python -m engine.board
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg

from core.config import EngineConfig
from core.db import connect
from engine.devig import devig_multiplicative
from engine.ev import expected_value

SHARP_BOOKMAKER = "pinnacle"


@dataclass(frozen=True)
class _Snap:
    bookmaker: str
    market: str
    outcome: str
    line: float | None
    price: float
    captured_at: datetime


def _latest_snapshots(conn: psycopg.Connection, event_id: UUID) -> list[_Snap]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select distinct on (bookmaker, market, outcome, coalesce(line, 'NaN'::numeric))
                   bookmaker, market, outcome, line, price, captured_at
            from odds_snapshots
            where event_id = %s
            order by bookmaker, market, outcome, coalesce(line, 'NaN'::numeric),
                     captured_at desc
            """,
            (event_id,),
        )
        return [
            _Snap(
                bookmaker=r[0],
                market=r[1],
                outcome=r[2],
                line=float(r[3]) if r[3] is not None else None,
                price=float(r[4]),
                captured_at=r[5],
            )
            for r in cur.fetchall()
        ]


def _upcoming_event_ids(conn: psycopg.Connection) -> list[UUID]:
    with conn.cursor() as cur:
        cur.execute("select id from events where status = 'scheduled' and commence_time > now()")
        return [r[0] for r in cur.fetchall()]


def candidates_for_event(snaps: list[_Snap], sharp_bookmaker: str = SHARP_BOOKMAKER) -> list[dict]:
    """Pure function: latest snapshots -> EV rows for every soft price that has
    a complete sharp market to reference. No thresholding here — the board
    stores raw EV; display layers apply config thresholds."""
    rows: list[dict] = []
    markets: dict[tuple[str, float | None], list[_Snap]] = {}
    for s in snaps:
        markets.setdefault((s.market, s.line), []).append(s)

    for (market, line), group in markets.items():
        sharp = {s.outcome: s for s in group if s.bookmaker == sharp_bookmaker}
        if len(sharp) < 2:
            continue  # no de-viggable sharp reference for this market/line
        probs = devig_multiplicative({o: s.price for o, s in sharp.items()})
        for s in group:
            if s.bookmaker == sharp_bookmaker or s.outcome not in probs:
                continue
            p_true = probs[s.outcome]
            rows.append(
                {
                    "bookmaker": s.bookmaker,
                    "market": market,
                    "outcome": s.outcome,
                    "line": line,
                    "price": s.price,
                    "sharp_price": sharp[s.outcome].price,
                    "p_true": p_true,
                    "novig_price": 1.0 / p_true,
                    "ev": expected_value(p_true, s.price),
                    "captured_at": s.captured_at,
                }
            )
    return rows


def refresh_ev_board(conn: psycopg.Connection, cfg: EngineConfig | None = None) -> int:
    """Rebuild ev_board from the latest snapshots of upcoming events."""
    written = 0
    with conn.cursor() as cur:
        cur.execute("truncate ev_board")
        for event_id in _upcoming_event_ids(conn):
            for row in candidates_for_event(_latest_snapshots(conn, event_id)):
                cur.execute(
                    """
                    insert into ev_board
                      (event_id, bookmaker, market, outcome, line, price,
                       sharp_price, p_true, novig_price, ev, captured_at)
                    values (%(event_id)s, %(bookmaker)s, %(market)s, %(outcome)s,
                            %(line)s, %(price)s, %(sharp_price)s, %(p_true)s,
                            %(novig_price)s, %(ev)s, %(captured_at)s)
                    """,
                    {**row, "event_id": event_id},
                )
                written += 1
    conn.commit()
    return written


def main() -> None:
    with connect() as conn:
        n = refresh_ev_board(conn)
    print(f"ev board refreshed: {n} candidate rows")


if __name__ == "__main__":
    main()
