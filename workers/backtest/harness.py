"""Backtest harness with leakage guards (CLAUDE.md §10).

Rules enforced here, not left to discipline:

1. Walk-forward only — `walk_forward` rejects unsorted input; `run_backtest`
   evaluates events in strict `commence_time` order. No shuffles, ever.
2. Features are built via `FeatureModule.features(event_id, as_of)` where
   `as_of` is the decision snapshot's `captured_at` — the world as the model
   would have seen it at publish time, not at kickoff.
3. Prices are real captured snapshots for ONE named bookmaker. Never averages,
   never closing prices replayed as if they were open prices (`not is_closing`).
4. Every row a feature module reads must be strictly older than `as_of`.
   Feature modules expose `last_sources()` provenance; `audit_sources` raises
   `LeakageError` on any violation. Modules without provenance are refused.
5. Backtest outputs are artifacts: JSON with config hash, data range and git
   SHA, so every public claim is reproducible.

Settlement and CLV reuse the production graders (grading.settle,
grading.grade.novig_close) — the backtest grades exactly like real picks.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import psycopg

from core.config import EngineConfig
from core.protocols import Model
from core.types import FeatureVector
from engine.ev import publishable_ev
from grading.clv import clv as clv_decimal
from grading.grade import novig_close
from grading.settle import settle_pick


class LeakageError(RuntimeError):
    """A feature source is not strictly older than as_of (§10.4)."""


@dataclass(frozen=True)
class SourceRow:
    """Provenance of one row a FeatureModule read while building features."""

    table: str
    key: str
    ingested_at: datetime


class AuditedFeatureModule(Protocol):
    """FeatureModule that can prove where its features came from (§10.4)."""

    sport_id: str

    def features(self, event_id: str, as_of: datetime) -> FeatureVector: ...

    def last_sources(self) -> Sequence[SourceRow]: ...


def audit_sources(sources: Sequence[SourceRow], as_of: datetime) -> None:
    """§10.4: every joined row must be STRICTLY older than as_of."""
    for s in sources:
        if s.ingested_at >= as_of:
            raise LeakageError(
                f"{s.table}[{s.key}] ingested_at={s.ingested_at.isoformat()} >= "
                f"as_of={as_of.isoformat()} — joined rows must be strictly older (§10.4)"
            )


def walk_forward(
    times: Sequence[datetime], *, n_splits: int = 3, min_train: int = 1
) -> Iterator[tuple[list[int], list[int]]]:
    """Expanding-window chronological splits: train is always strictly before
    test. Unsorted input is rejected outright — random shuffles are forbidden
    (§10.1). Yields (train_indices, test_indices) per fold."""
    for a, b in zip(times, times[1:], strict=False):
        if b < a:
            raise ValueError("walk-forward requires chronologically sorted data (§10.1)")
    n = len(times)
    if n_splits < 1 or min_train < 1 or n - min_train < n_splits:
        raise ValueError(f"cannot cut {n} rows into {n_splits} splits after {min_train} train rows")
    test_len = (n - min_train) // n_splits
    for i in range(n_splits):
        start = min_train + i * test_len
        end = n if i == n_splits - 1 else start + test_len
        yield list(range(start)), list(range(start, end))


@dataclass(frozen=True)
class BacktestPick:
    """One simulated flat-stake (1.0 unit) pick, settled like production."""

    event_id: str
    commence_time: datetime
    as_of: datetime
    market: str
    outcome: str
    line: float | None
    bookmaker: str
    price: float
    p_model: float
    ev: float
    result: str
    pnl_units: float
    clv: float | None


@dataclass
class BacktestReport:
    model_id: str
    sport_id: str
    market: str
    line: float | None
    bookmaker: str
    start: datetime
    end: datetime
    n_events: int = 0
    n_events_priced: int = 0
    picks: list[BacktestPick] = field(default_factory=list)


@dataclass(frozen=True)
class _Snap:
    price: float
    captured_at: datetime


def _decision_prices(
    conn: psycopg.Connection,
    event_id: Any,
    bookmaker: str,
    market: str,
    line: float | None,
    commence: datetime,
) -> dict[str, _Snap]:
    """Latest pre-kickoff, non-closing price per outcome at the NAMED
    bookmaker (§10.3: real captured prices; the close is never the open)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select distinct on (outcome) outcome, price, captured_at
            from odds_snapshots
            where event_id = %s and bookmaker = %s and market = %s
              and coalesce(line, 'NaN'::numeric) = coalesce(%s, 'NaN'::numeric)
              and not is_closing
              and captured_at < %s
            order by outcome, captured_at desc
            """,
            (event_id, bookmaker, market, line, commence),
        )
        return {r[0]: _Snap(float(r[1]), r[2]) for r in cur.fetchall()}


def run_backtest(
    conn: psycopg.Connection,
    feature_module: AuditedFeatureModule,
    model: Model,
    *,
    sport_id: str,
    bookmaker: str,
    market: str,
    start: datetime,
    end: datetime,
    line: float | None = None,
    cfg: EngineConfig | None = None,
) -> BacktestReport:
    """Replay finished events chronologically; publish where the model saw
    edge at the named bookmaker's captured price; settle like production."""
    if not hasattr(feature_module, "last_sources"):
        raise LeakageError(
            "feature module has no last_sources() provenance — the §10.4 audit "
            "is impossible, refusing to run"
        )
    cfg = cfg or EngineConfig()
    with conn.cursor() as cur:
        cur.execute(
            """
            select e.id, e.commence_time, r.home_score, r.away_score
            from events e
            join event_results r on r.event_id = e.id
            where e.sport_id = %s
              and e.commence_time >= %s and e.commence_time < %s
            order by e.commence_time, e.id
            """,
            (sport_id, start, end),
        )
        events = cur.fetchall()

    report = BacktestReport(
        model_id=model.model_id,
        sport_id=sport_id,
        market=market,
        line=line,
        bookmaker=bookmaker,
        start=start,
        end=end,
        n_events=len(events),
    )
    for event_id, commence, hs, aw in events:  # chronological (§10.1)
        prices = _decision_prices(conn, event_id, bookmaker, market, line, commence)
        if len(prices) < 2:
            continue
        report.n_events_priced += 1
        as_of = max(s.captured_at for s in prices.values())
        fv = feature_module.features(str(event_id), as_of)
        audit_sources(feature_module.last_sources(), as_of)
        probs = model.predict(fv)
        for outcome in sorted(prices):
            p = probs.get(outcome)
            if p is None or not 0.0 < p < 1.0:
                continue
            snap = prices[outcome]
            ev = publishable_ev(p, snap.price, cfg)
            if ev is None:
                continue
            result, pnl = settle_pick(market, outcome, line, int(hs), int(aw), 1.0, snap.price)
            close = novig_close(conn, event_id, market, outcome, line)
            report.picks.append(
                BacktestPick(
                    event_id=str(event_id),
                    commence_time=commence,
                    as_of=as_of,
                    market=market,
                    outcome=outcome,
                    line=line,
                    bookmaker=bookmaker,
                    price=snap.price,
                    p_model=p,
                    ev=ev,
                    result=result,
                    pnl_units=pnl,
                    clv=clv_decimal(snap.price, close) if close is not None else None,
                )
            )
    return report


# ------------------------------------------------------------------ artifacts


def config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _calibration(picks: Sequence[BacktestPick], n_bins: int = 10) -> list[dict[str, float]]:
    """Predicted probability vs realized win rate, decided picks only."""
    bins: list[dict[str, float]] = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        hits = [
            p
            for p in picks
            if p.result in ("win", "loss") and lo <= p.p_model < (hi if i < n_bins - 1 else 1.01)
        ]
        if not hits:
            continue
        bins.append(
            {
                "bin_low": lo,
                "bin_high": hi,
                "n": len(hits),
                "mean_p": sum(p.p_model for p in hits) / len(hits),
                "win_rate": sum(1 for p in hits if p.result == "win") / len(hits),
            }
        )
    return bins


def build_artifact(report: BacktestReport, config: dict[str, Any]) -> dict[str, Any]:
    """§10.5: reproducible artifact — config hash, data range, git SHA."""
    picks = report.picks
    clvs = [p.clv for p in picks if p.clv is not None]
    wins = sum(1 for p in picks if p.result == "win")
    losses = sum(1 for p in picks if p.result == "loss")
    pushes = sum(1 for p in picks if p.result == "push")
    return {
        "kind": "backtest",
        "model_id": report.model_id,
        "sport_id": report.sport_id,
        "market": report.market,
        "line": report.line,
        "bookmaker": report.bookmaker,
        "data_range": {"start": report.start.isoformat(), "end": report.end.isoformat()},
        "git_sha": git_sha(),
        "config": config,
        "config_hash": config_hash(config),
        "created_at": datetime.now(UTC).isoformat(),
        "n_events": report.n_events,
        "n_events_priced": report.n_events_priced,
        "n_picks": len(picks),
        "record": {"win": wins, "loss": losses, "push": pushes},
        "flat_stake_roi": (sum(p.pnl_units for p in picks) / len(picks)) if picks else None,
        "clv": {
            "n": len(clvs),
            "mean": (sum(clvs) / len(clvs)) if clvs else None,
        },
        "calibration": _calibration(picks),
        "picks": [
            {
                "event_id": p.event_id,
                "commence_time": p.commence_time.isoformat(),
                "as_of": p.as_of.isoformat(),
                "market": p.market,
                "outcome": p.outcome,
                "line": p.line,
                "bookmaker": p.bookmaker,
                "price": p.price,
                "p_model": round(p.p_model, 6),
                "ev": round(p.ev, 6),
                "result": p.result,
                "pnl_units": round(p.pnl_units, 4),
                "clv": round(p.clv, 6) if p.clv is not None else None,
            }
            for p in picks
        ],
    }


def write_artifact(artifact: dict[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    start = artifact["data_range"]["start"][:10].replace("-", "")
    end = artifact["data_range"]["end"][:10].replace("-", "")
    path = out / f"{artifact['model_id']}_{start}-{end}_{artifact['config_hash'][:8]}.json"
    path.write_text(json.dumps(artifact, indent=2, default=str) + "\n")
    return path
