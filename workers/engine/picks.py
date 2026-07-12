"""Layer-1 shadow pick publisher (CLAUDE.md §2, §8, §9).

Turns +EV board rows into immutable picks under per-sport market models
(`market_ev_v1_<sport>`, status `shadow`). This is the Layer-1 track record:
no sport expertise, just de-vigged sharp reference vs soft prices.

Rules encoded here:
- publish only pre-match (`commence_time > now()`) — §2.1 timestamped pre-match;
- threshold: EV minus margin haircut must clear cfg.ev_threshold (config, not code);
- stake: quarter-Kelly via engine.kelly, hard-capped — never full Kelly;
- one pick per (model, event, market, outcome, line): best-EV bookmaker wins,
  re-runs are no-ops through the picks_publish_uniq index (INSERT ... ON
  CONFLICT DO NOTHING — never an update, picks stay append-only);
- features_hash = sha256 of the exact board row used, so every pick is
  reproducible (§2.1).

Shadow -> public happens ONLY via the §9 gates; nothing here flips status.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg

from core.config import EngineConfig
from engine.kelly import stake_units

MODEL_VERSION = "v1"


def market_model_id(sport_id: str) -> str:
    return f"market_ev_{MODEL_VERSION}_{sport_id}"


def ensure_market_models(conn: psycopg.Connection, cfg: EngineConfig) -> list[str]:
    """Register one shadow market model per sport (idempotent). The engine
    config is stored on the row so published picks are reproducible."""
    config = {
        "ev_threshold": cfg.ev_threshold,
        "margin_haircut": cfg.margin_haircut,
        "kelly_fraction": cfg.kelly_fraction,
        "max_stake_units": cfg.max_stake_units,
        "sharp_reference": "pinnacle",
        "devig": "multiplicative",
    }
    ids: list[str] = []
    with conn.cursor() as cur:
        cur.execute("select id from sports order by id")
        for (sport_id,) in cur.fetchall():
            mid = market_model_id(sport_id)
            cur.execute(
                """
                insert into models (id, sport_id, market, version, status, config)
                values (%s, %s, 'multi', %s, 'shadow', %s)
                on conflict (id) do nothing
                """,
                (mid, sport_id, MODEL_VERSION, json.dumps(config)),
            )
            ids.append(mid)
    return ids


def _features_hash(payload: dict[str, Any]) -> str:
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


def publish_from_board(conn: psycopg.Connection, cfg: EngineConfig) -> int:
    """Publish every board edge that clears the haircut threshold. Returns the
    number of picks actually inserted (conflicts and re-runs count as 0)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select b.event_id, e.sport_id, b.bookmaker, b.market, b.outcome,
                   b.line, b.price, b.sharp_price, b.p_true, b.novig_price,
                   b.ev, b.captured_at
            from ev_board b
            join events e on e.id = b.event_id
            where e.commence_time > now()
            order by b.ev desc
            """
        )
        rows = cur.fetchall()

    published = 0
    with conn.cursor() as cur:
        for (
            event_id,
            sport_id,
            bookmaker,
            market,
            outcome,
            line,
            price,
            sharp_price,
            p_true,
            novig_price,
            ev,
            captured_at,
        ) in rows:
            if float(ev) - cfg.margin_haircut < cfg.ev_threshold:
                continue
            stake = stake_units(float(p_true), float(price), cfg)
            if stake <= 0.0:
                continue
            payload = {
                "event_id": str(event_id),
                "bookmaker": bookmaker,
                "market": market,
                "outcome": outcome,
                "line": float(line) if line is not None else None,
                "price": float(price),
                "sharp_price": float(sharp_price),
                "p_true": float(p_true),
                "novig_price": float(novig_price),
                "ev": float(ev),
                "captured_at": captured_at,
            }
            cur.execute(
                """
                insert into picks
                  (model_id, event_id, market, outcome, line, price_at_publish,
                   bookmaker, stake_units, features_hash)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                returning id
                """,
                (
                    market_model_id(sport_id),
                    event_id,
                    market,
                    outcome,
                    line,
                    price,
                    bookmaker,
                    round(stake, 4),
                    _features_hash(payload),
                ),
            )
            row = cur.fetchone()
            if row is None:
                continue  # already published (idempotent re-run)
            cur.execute(
                """
                insert into pick_features (pick_id, payload)
                values (%s, %s) on conflict do nothing
                """,
                (row[0], json.dumps(payload, default=str)),
            )
            published += 1
    return published
