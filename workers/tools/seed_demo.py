"""Seed a LOCAL dev database with SYNTHETIC demo data.

Purpose: exercise the full pipeline end-to-end (odds snapshots -> de-vig ->
+EV board -> picks -> closing-line capture -> CLV grading) so the web UI has
something to display before real collectors are unblocked.

Everything here is fake: prices are generated, results are scripted, and the
demo model is registered as status='shadow' (a demo can NEVER be 'public' —
the §9 gates exist for real models only).

Idempotence: picks are append-only by design, so this script refuses to run
against a database that already contains the demo model. Recreate the dev DB
to reseed:  dropdb sportbets_dev && createdb sportbets_dev && <apply migrations>

Run: uv run python -m tools.seed_demo
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg

from core.config import EngineConfig
from core.db import connect, insert_snapshots, mark_closing_lines
from core.types import OddsSnapshot
from engine.devig import devig_multiplicative, novig_price
from engine.kelly import stake_units
from grading.clv import clv as clv_of
from grading.clv import pnl_units

RNG = random.Random(42)
PROVIDER = "demo"
MODEL_ID = "demo_ev_v0"
SHARP = "pinnacle"
SOFTS = ["bet365", "unibet", "betano"]
SHARP_VIG = 0.025
SOFT_VIG = 0.06

NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

# (sport, competition, country, tier, home, away, hours_from_now, p_home_true, result)
# result: 'home'/'away' for finished events, None for upcoming
DEMO_EVENTS: list[tuple] = [
    # upcoming — feed the +EV board
    ("cs2", "ESL Pro League S22", None, 1, "Natus Vincere", "FaZe Clan", 3, 0.58, None),
    ("cs2", "ESL Pro League S22", None, 1, "Team Vitality", "G2 Esports", 8, 0.62, None),
    ("football", "Liga I", "RO", 1, "FCSB", "CFR Cluj", 26, 0.47, None),
    ("football", "Eredivisie", "NL", 1, "Ajax", "AZ Alkmaar", 50, 0.55, None),
    # finished — feed picks / settlements / track record
    ("cs2", "ESL Pro League S22", None, 1, "MOUZ", "Team Spirit", -30, 0.52, "home"),
    ("cs2", "ESL Pro League S22", None, 1, "Astralis", "Heroic", -26, 0.44, "away"),
    ("football", "Liga I", "RO", 1, "Universitatea Craiova", "Rapid Bucuresti", -20, 0.51, "away"),
    ("football", "Eredivisie", "NL", 1, "PSV", "Feyenoord", -8, 0.60, "home"),
]

SNAPSHOT_OFFSETS_H = [48.0, 36.0, 24.0, 12.0, 6.0, 2.0, 1.0, 0.5, 0.1]


def _prices(p_home: float, vig: float, jitter: float) -> dict[str, float]:
    """Two-way market prices with margin `vig`, multiplicative."""
    p = min(0.95, max(0.05, p_home + RNG.uniform(-jitter, jitter)))
    return {
        "home": round(1.0 / (p * (1.0 + vig)), 3),
        "away": round(1.0 / ((1.0 - p) * (1.0 + vig)), 3),
    }


def _ensure_fresh(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("select 1 from models where id = %s", (MODEL_ID,))
        if cur.fetchone():
            raise SystemExit(
                f"demo data already present (model {MODEL_ID}); picks are append-only — "
                "recreate the dev database to reseed (see module docstring)"
            )


def _seed_reference(conn: psycopg.Connection) -> dict[str, UUID]:
    """Competitions + teams; returns team name -> id."""
    comp_ids: dict[str, UUID] = {}
    team_ids: dict[str, UUID] = {}
    with conn.cursor() as cur:
        for sport, comp, country, tier, home, away, *_ in DEMO_EVENTS:
            if comp not in comp_ids:
                cur.execute(
                    """insert into competitions (sport_id, name, country, tier)
                       values (%s, %s, %s, %s) returning id""",
                    (sport, comp, country, tier),
                )
                comp_ids[comp] = cur.fetchone()[0]
            for name in (home, away):
                if name not in team_ids:
                    cur.execute(
                        """insert into teams (sport_id, canonical_name, country)
                           values (%s, %s, %s) returning id""",
                        (sport, name, country),
                    )
                    team_ids[name] = cur.fetchone()[0]
        cur.execute(
            """insert into models (id, sport_id, market, version, status, config)
               values (%s, 'cs2', 'h2h', 'v0', 'shadow',
                       '{"note": "synthetic demo model — not a real track record"}')""",
            (MODEL_ID,),
        )
    return {**team_ids, **{f"comp:{k}": v for k, v in comp_ids.items()}}


def _seed_event(
    conn: psycopg.Connection, ids: dict[str, UUID], row: tuple
) -> tuple[UUID, datetime, float, str | None, str, str]:
    sport, comp, _country, _tier, home, away, hours, p_home, result = row
    commence = NOW + timedelta(hours=hours)
    status = "finished" if result else "scheduled"
    with conn.cursor() as cur:
        cur.execute(
            """insert into events (sport_id, competition_id, home_team, away_team,
                                   commence_time, status, provider_keys)
               values (%s, %s, %s, %s, %s, %s, %s::jsonb) returning id""",
            (
                sport,
                ids[f"comp:{comp}"],
                ids[home],
                ids[away],
                commence,
                status,
                json.dumps({PROVIDER: f"{home}-{away}".lower().replace(" ", "_")}),
            ),
        )
        event_id = cur.fetchone()[0]
    return event_id, commence, p_home, result, home, away


def _seed_odds(
    conn: psycopg.Connection, event_id: UUID, commence: datetime, p_home: float
) -> dict[str, dict[str, float]]:
    """Time series of h2h snapshots. Returns the LAST pre-kickoff prices per book.
    One soft book gets a deliberately stale/high home price -> +EV candidate."""
    snaps: list[OddsSnapshot] = []
    last: dict[str, dict[str, float]] = {}
    generous_book = RNG.choice(SOFTS)
    for off in SNAPSHOT_OFFSETS_H:
        at = commence - timedelta(hours=off)
        if at > datetime.now(UTC):
            continue  # don't fabricate future observations
        drift = (48.0 - off) / 48.0 * 0.02  # market slowly sharpens toward p_home
        p_now = p_home + drift
        books = {SHARP: _prices(p_now, SHARP_VIG, 0.005)}
        for soft in SOFTS:
            books[soft] = _prices(p_now, SOFT_VIG, 0.015)
        # the generous book lags the move: prices its home line off the opener
        books[generous_book]["home"] = round(1.0 / (p_home * (1.0 + SOFT_VIG)) * 1.10, 3)
        for book, prices in books.items():
            last[book] = prices
            for outcome, price in prices.items():
                snaps.append(
                    OddsSnapshot(
                        provider=PROVIDER,
                        event_provider_key=str(event_id),
                        bookmaker=book,
                        market="h2h",
                        outcome=outcome,
                        price=price,
                        captured_at=at,
                    )
                )
    insert_snapshots(conn, event_id, snaps)
    return last


def _seed_pick_and_settlement(
    conn: psycopg.Connection,
    cfg: EngineConfig,
    event_id: UUID,
    commence: datetime,
    result: str,
    last_prices: dict[str, dict[str, float]],
    home: str,
    away: str,
) -> None:
    """Publish one demo pick (as of T-6h prices) and grade it vs the no-vig close."""
    sharp_close = last_prices[SHARP]
    probs_close = devig_multiplicative(sharp_close)
    # pick the softest home price available at publish time (synthetic +EV)
    book, price = max(
        ((b, p["home"]) for b, p in last_prices.items() if b != SHARP),
        key=lambda bp: bp[1],
    )
    p_true = probs_close["home"]
    stake = round(stake_units(p_true, price, cfg), 2) or 0.25
    features = {"p_true": round(p_true, 4), "price": price, "bookmaker": book}
    with conn.cursor() as cur:
        cur.execute(
            """insert into picks (model_id, event_id, market, outcome,
                                  price_at_publish, bookmaker, stake_units,
                                  published_at, rationale, features_hash)
               values (%s, %s, 'h2h', 'home', %s, %s, %s, %s, %s, %s)
               returning id""",
            (
                MODEL_ID,
                event_id,
                price,
                book,
                stake,
                commence - timedelta(hours=6),
                f"[SYNTHETIC DEMO] {book} priced {home} at {price} vs a no-vig fair "
                f"price of {novig_price(p_true):.2f} derived from the sharp reference. "
                f"Educational example only — {home} vs {away}.",
                hashlib.sha256(json.dumps(features, sort_keys=True).encode()).hexdigest(),
            ),
        )
        pick_id = cur.fetchone()[0]
        close_fair = novig_price(p_true)
        outcome_result = "win" if result == "home" else "loss"
        cur.execute(
            """insert into settlements (pick_id, result, closing_price, clv,
                                        pnl_units, settled_at)
               values (%s, %s, %s, %s, %s, %s)""",
            (
                pick_id,
                outcome_result,
                round(close_fair, 3),
                round(clv_of(price, close_fair), 4),
                round(pnl_units(outcome_result, stake, price), 3),
                commence + timedelta(hours=2),
            ),
        )


def main() -> None:
    cfg = EngineConfig.from_env()
    with connect() as conn:
        _ensure_fresh(conn)
        ids = _seed_reference(conn)
        n_events = 0
        n_picks = 0
        for row in DEMO_EVENTS:
            event_id, commence, p_home, result, home, away = _seed_event(conn, ids, row)
            last_prices = _seed_odds(conn, event_id, commence, p_home)
            n_events += 1
            if result:
                mark_closing_lines(conn, event_id)
                _seed_pick_and_settlement(
                    conn, cfg, event_id, commence, result, last_prices, home, away
                )
                n_picks += 1
        conn.commit()
    print(f"seeded {n_events} demo events, {n_picks} graded demo picks")
    print("next: uv run python -m engine.board")


if __name__ == "__main__":
    main()
