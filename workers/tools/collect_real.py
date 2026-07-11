"""Run one REAL collection cycle into the configured database.

Usage:
    uv run python -m tools.collect_real            # OddsPapi CS2 (BLAST) only
    uv run python -m tools.collect_real --therundown  # + TheRundown soccer

Request budget (OddsPapi free tier ~250 req/mo): one run costs
1 fixtures request + 1 odds request PER bookmaker (quirk: one bookmaker per
odds call). Default = 3 requests. Don't loop this casually.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from collectors.base import collect_once
from collectors.oddspapi import OddsPapiCollector, load_markets_ref
from collectors.therundown import TheRundownCollector
from core.config import EngineConfig
from core.db import connect
from engine.board import refresh_ev_board

# BLAST Premier Series (verified in samples/oddspapi_tournaments_cs2.json)
BLAST_PREMIER = 31621
# pinnacle = sharp reference; superbet.ro = EU soft book (slug verified in
# samples/oddspapi_bookmakers.json)
BOOKMAKERS = ["pinnacle", "superbet.ro"]

MARKETS_REF = (
    Path(__file__).resolve().parents[2] / "docs" / "providers" / "samples" / "oddspapi_markets.json"
)


def main(argv: list[str]) -> int:
    now = datetime.now(UTC)
    conn = connect()
    try:
        oddspapi = OddsPapiCollector(
            tournament_ids=[BLAST_PREMIER],
            bookmakers=BOOKMAKERS,
            markets_ref=load_markets_ref(MARKETS_REF),
        )
        n_fix, n_snap = collect_once(conn, oddspapi, now, now + timedelta(days=14))
        print(f"oddspapi: {n_fix} fixtures, {n_snap} snapshots")

        if "--therundown" in argv:
            # Sport 18 = FIFA World Cup (in season 2026-07). Keep the day
            # window small — free tier bills per data point, not per request.
            therundown = TheRundownCollector(sport_ids=[18])
            n_fix, n_snap = collect_once(conn, therundown, now, now + timedelta(days=2))
            print(f"therundown: {n_fix} fixtures, {n_snap} snapshots")

        n_rows = refresh_ev_board(conn, EngineConfig.from_env())
        print(f"ev_board: {n_rows} rows")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
