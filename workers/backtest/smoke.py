"""Harness smoke test on real captured Layer-1 snapshots (§13 Task 3).

SharpRefFeatureModule turns the last Pinnacle snapshot STRICTLY before as_of
into no-vig probabilities (with full row provenance for the §10.4 audit), and
SharpMirrorModel simply mirrors them. Run against a soft bookmaker this
reproduces the Layer-1 +EV logic inside the audited harness; run against
Pinnacle itself it should find ~no edge. The point is to prove the harness
end-to-end on real data — NOT to validate a model.

Run: uv run python -m backtest.smoke [--sport cs2] [--market h2h]
     [--bookmaker <book>] [--days 30] [--out artifacts/backtests]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

import psycopg

from backtest.harness import (
    SourceRow,
    build_artifact,
    run_backtest,
    write_artifact,
)
from core.config import EngineConfig
from core.db import connect
from core.types import FeatureVector
from engine.board import SHARP_BOOKMAKER
from engine.devig import devig_multiplicative


class SharpRefFeatureModule:
    """Features = de-vigged sharp reference as of strictly-before-as_of
    snapshots. Provenance recorded per row for the leakage audit."""

    def __init__(
        self,
        conn: psycopg.Connection,
        sport_id: str,
        market: str = "h2h",
        line: float | None = None,
        sharp_bookmaker: str = SHARP_BOOKMAKER,
    ) -> None:
        self.conn = conn
        self.sport_id = sport_id
        self.market = market
        self.line = line
        self.sharp_bookmaker = sharp_bookmaker
        self._sources: list[SourceRow] = []

    def features(self, event_id: str, as_of: datetime) -> FeatureVector:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                select distinct on (outcome) outcome, price, id, captured_at
                from odds_snapshots
                where event_id = %s and bookmaker = %s and market = %s
                  and coalesce(line, 'NaN'::numeric) = coalesce(%s, 'NaN'::numeric)
                  and captured_at < %s
                order by outcome, captured_at desc
                """,
                (event_id, self.sharp_bookmaker, self.market, self.line, as_of),
            )
            rows = cur.fetchall()
        self._sources = [SourceRow("odds_snapshots", str(r[2]), r[3]) for r in rows]
        features: dict[str, float | int | str | None] = {}
        if len(rows) >= 2:
            probs = devig_multiplicative({r[0]: float(r[1]) for r in rows})
            features = {f"novig_{o}": p for o, p in probs.items()}
        return FeatureVector(
            sport_id=self.sport_id, event_id=str(event_id), as_of=as_of, features=features
        )

    def last_sources(self) -> list[SourceRow]:
        return list(self._sources)


class SharpMirrorModel:
    """Predicts exactly the sharp no-vig probabilities from the features."""

    model_id = "smoke_sharp_mirror_v0"

    def predict(self, fv: FeatureVector) -> dict[str, float]:
        return {
            k.removeprefix("novig_"): float(v)
            for k, v in fv.features.items()
            if k.startswith("novig_") and isinstance(v, int | float)
        }


def busiest_soft_book(
    conn: psycopg.Connection, sport_id: str, market: str, sharp: str = SHARP_BOOKMAKER
) -> str | None:
    """Most-captured non-sharp bookmaker for this sport/market."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select os.bookmaker, count(*) as n
            from odds_snapshots os
            join events e on e.id = os.event_id
            where e.sport_id = %s and os.market = %s and os.bookmaker <> %s
            group by os.bookmaker
            order by n desc
            limit 1
            """,
            (sport_id, market, sharp),
        )
        row = cur.fetchone()
    return row[0] if row else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sport", default="cs2")
    ap.add_argument("--market", default="h2h")
    ap.add_argument("--bookmaker", default=None, help="default: busiest soft book captured")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default="artifacts/backtests")
    args = ap.parse_args()

    end = datetime.now(UTC)
    start = end - timedelta(days=args.days)
    cfg = EngineConfig.from_env()

    with connect() as conn:
        bookmaker = args.bookmaker or busiest_soft_book(conn, args.sport, args.market)
        if not bookmaker:
            print(
                f"smoke: no soft-book {args.market} snapshots for {args.sport} yet — "
                "nothing to replay"
            )
            return
        fm = SharpRefFeatureModule(conn, args.sport, market=args.market)
        model = SharpMirrorModel()
        report = run_backtest(
            conn,
            fm,
            model,
            sport_id=args.sport,
            bookmaker=bookmaker,
            market=args.market,
            start=start,
            end=end,
            cfg=cfg,
        )

    artifact = build_artifact(
        report,
        config={
            "feature_module": "SharpRefFeatureModule",
            "model": model.model_id,
            "sport_id": args.sport,
            "market": args.market,
            "bookmaker": bookmaker,
            "sharp_bookmaker": SHARP_BOOKMAKER,
            "ev_threshold": cfg.ev_threshold,
            "margin_haircut": cfg.margin_haircut,
        },
    )
    path = write_artifact(artifact, args.out)
    clv_mean = artifact["clv"]["mean"]
    print(
        f"smoke backtest [{args.sport}/{args.market} @ {bookmaker}] "
        f"{report.start:%Y-%m-%d}..{report.end:%Y-%m-%d}: "
        f"{artifact['n_events']} events, {artifact['n_events_priced']} priced, "
        f"{artifact['n_picks']} picks, record {artifact['record']}, "
        f"flat ROI {artifact['flat_stake_roi']}, "
        f"mean CLV {clv_mean if clv_mean is not None else 'n/a'} "
        f"(n={artifact['clv']['n']})"
    )
    print(f"artifact: {path}")


if __name__ == "__main__":
    main()
