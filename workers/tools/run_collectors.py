"""Cadence-aware collection daemon (CLAUDE.md §8).

Per tick (60s): for each provider, poll if enough time has passed given
(a) the §8 cadence band of its NEAREST upcoming event and (b) a per-provider
budget floor; then run closing-line capture and refresh the +EV board.

Budget floors (env-overridable — config, not code):
- ODDSPAPI_MIN_POLL_HOURS  (default 12): free tier ~250 req/mo and each poll
  costs 1 fixtures request per tournament + 1 odds request per bookmaker.
- THERUNDOWN_MIN_POLL_HOURS (default 1): 20K data points/day is roomy for a
  single sport, but each extra sport/day multiplies the burn.

Usage:
    uv run python -m tools.run_collectors --once   # single tick (cron-friendly)
    uv run python -m tools.run_collectors          # loop forever (systemd)
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from adapters.football import LIGA_I_LEAGUE_ID, ApiFootballAdapter
from adapters.football import PROVIDER as API_FOOTBALL
from agents.explainer import explain_pending
from collectors.base import collect_once, persist_fixtures, poll_interval
from collectors.closing import run_closing_capture
from collectors.oddspapi import OddsPapiCollector, load_markets_ref
from collectors.therundown import TheRundownCollector
from core.config import EngineConfig
from core.db import (
    connect,
    get_last_poll,
    next_provider_kickoff,
    persist_results,
    set_last_poll,
)
from core.protocols import OddsCollector, SportAdapter
from engine.board import refresh_ev_board
from engine.picks import ensure_market_models, publish_from_board
from grading.grade import grade_picks
from resolver.resolver import merge_duplicate_events, resolve_events

TICK_SECONDS = 60.0
# No known upcoming events -> still poll at this pace to discover fixtures.
DISCOVERY_INTERVAL = timedelta(hours=24)
# How far back a results poll looks for finished fixtures to grade.
RESULTS_LOOKBACK = timedelta(days=7)
# §11: explainer runs in nightly batches.
EXPLAINER_INTERVAL = timedelta(hours=24)

_SAMPLES = Path(__file__).resolve().parents[2] / "docs" / "providers" / "samples"


@dataclass(frozen=True)
class Schedule:
    collector: OddsCollector
    min_interval: timedelta  # provider budget floor, overrides cadence near kickoff
    horizon: timedelta  # how far ahead fixtures/odds are requested


@dataclass(frozen=True)
class FixtureSchedule:
    """Stats-side fixture ingest (SportAdapter, no odds). Fixed interval:
    schedules change slowly, so the §8 odds cadence does not apply."""

    adapter: SportAdapter
    provider: str
    interval: timedelta
    horizon: timedelta


def effective_interval(
    nearest_kickoff: datetime | None,
    now: datetime,
    min_interval: timedelta,
) -> timedelta:
    """§8 cadence for the provider's nearest event, floored by budget."""
    if nearest_kickoff is None:
        cadence = DISCOVERY_INTERVAL
    else:
        cadence = poll_interval(nearest_kickoff - now) or timedelta(minutes=2)
    return max(cadence, min_interval)


def is_due(
    last_polled_at: datetime | None,
    nearest_kickoff: datetime | None,
    now: datetime,
    min_interval: timedelta,
) -> bool:
    if last_polled_at is None:
        return True
    return now - last_polled_at >= effective_interval(nearest_kickoff, now, min_interval)


def run_tick(
    conn: psycopg.Connection,
    schedules: list[Schedule],
    cfg: EngineConfig,
    fixture_schedules: list[FixtureSchedule] | None = None,
) -> bool:
    """One scheduler pass. Returns True if any provider was polled."""
    polled = False

    for fs in fixture_schedules or []:
        now = datetime.now(UTC)
        last = get_last_poll(conn, fs.provider)
        if last is not None and now - last < fs.interval:
            continue
        try:
            fixtures = fs.adapter.fixtures(now, now + fs.horizon)
            results = fs.adapter.results(now - RESULTS_LOOKBACK)
        except Exception as exc:  # noqa: BLE001 — daemon must survive provider hiccups
            conn.rollback()
            print(f"[{now:%H:%M:%S}] {fs.provider}: fixtures FAILED: {exc!r}", file=sys.stderr)
            continue
        n_fix = persist_fixtures(conn, fixtures)
        n_res = persist_results(conn, results)
        set_last_poll(conn, fs.provider, now)
        conn.commit()
        print(f"[{now:%H:%M:%S}] {fs.provider}: {n_fix} fixtures, {n_res} results (stats side)")
        polled = True

    for s in schedules:
        now = datetime.now(UTC)
        provider = s.collector.provider
        nearest = next_provider_kickoff(conn, provider, now)
        if not is_due(get_last_poll(conn, provider), nearest, now, s.min_interval):
            continue
        try:
            n_fix, n_snap = collect_once(conn, s.collector, now, now + s.horizon)
        except Exception as exc:  # noqa: BLE001 — daemon must survive provider hiccups
            conn.rollback()
            print(f"[{now:%H:%M:%S}] {provider}: poll FAILED: {exc!r}", file=sys.stderr)
            continue
        set_last_poll(conn, provider, now)
        conn.commit()
        print(f"[{now:%H:%M:%S}] {provider}: {n_fix} fixtures, {n_snap} snapshots")
        polled = True

    if polled:
        # Entity resolution after fresh fixtures: link raw team names, then
        # merge cross-provider duplicates (§7). Ambiguous names go to the
        # review queue (tools/review.py) and stay blocked, never guessed.
        counters = resolve_events(conn)
        merged = merge_duplicate_events(conn)
        conn.commit()
        if counters["sides_resolved"] or counters["sides_queued"] or merged:
            print(
                f"resolver: {counters['sides_resolved']} sides linked, "
                f"{counters['sides_queued']} queued for review, {merged} events merged"
            )

    marked = run_closing_capture(conn)
    if polled or marked:
        n_rows = refresh_ev_board(conn, cfg)
        # Layer-1 shadow track record: board edges above the config threshold
        # become immutable picks; the grader settles what has results (§2, §8).
        ensure_market_models(conn, cfg)
        n_picks = publish_from_board(conn, cfg)
        n_graded = grade_picks(conn)
        conn.commit()
        flagged = sum(marked.values())
        print(
            f"closing: {flagged} snapshots flagged; ev_board: {n_rows} rows; "
            f"picks: +{n_picks} published, {n_graded} graded"
        )

    # §11: explainer runs as a nightly batch; failures never block collection.
    now = datetime.now(UTC)
    last_explained = get_last_poll(conn, "explainer")
    if os.environ.get("ANTHROPIC_API_KEY") and (
        last_explained is None or now - last_explained >= EXPLAINER_INTERVAL
    ):
        try:
            n_expl = explain_pending(conn)
            set_last_poll(conn, "explainer", now)
            conn.commit()
            if n_expl:
                print(f"explainer: {n_expl} rationales stored")
        except Exception as exc:  # noqa: BLE001 — daemon must survive API hiccups
            conn.rollback()
            print(f"explainer FAILED: {exc!r}", file=sys.stderr)

    return polled


def _csv_env(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


def build_schedules() -> list[Schedule]:
    oddspapi = OddsPapiCollector(
        tournament_ids=[int(t) for t in _csv_env("ODDSPAPI_TOURNAMENT_IDS", "31621")],
        bookmakers=_csv_env("ODDSPAPI_BOOKMAKERS", "pinnacle,superbet.ro"),
        markets_ref=load_markets_ref(_SAMPLES / "oddspapi_markets.json"),
    )
    therundown = TheRundownCollector(
        sport_ids=[int(s) for s in _csv_env("THERUNDOWN_SPORT_IDS", "18")],
    )
    return [
        Schedule(
            collector=oddspapi,
            min_interval=timedelta(hours=float(os.environ.get("ODDSPAPI_MIN_POLL_HOURS", "12"))),
            horizon=timedelta(days=14),
        ),
        Schedule(
            collector=therundown,
            min_interval=timedelta(hours=float(os.environ.get("THERUNDOWN_MIN_POLL_HOURS", "1"))),
            # Each day in the window is one request per sport — keep it short.
            horizon=timedelta(days=2),
        ),
    ]


def build_fixture_schedules() -> list[FixtureSchedule]:
    football = ApiFootballAdapter(
        league_ids=[int(x) for x in _csv_env("API_FOOTBALL_LEAGUE_IDS", str(LIGA_I_LEAGUE_ID))],
        season=int(os.environ.get("API_FOOTBALL_SEASON", "2026")),
    )
    return [
        FixtureSchedule(
            adapter=football,
            provider=API_FOOTBALL,
            # One request per league per poll against 7,500/day (Pro) — daily
            # is generous; fixtures barely move intra-day.
            interval=timedelta(hours=float(os.environ.get("API_FOOTBALL_MIN_POLL_HOURS", "24"))),
            horizon=timedelta(days=14),
        )
    ]


def main(argv: list[str]) -> int:
    schedules = build_schedules()
    fixture_schedules = build_fixture_schedules()
    cfg = EngineConfig.from_env()
    conn = connect()
    try:
        if "--once" in argv:
            run_tick(conn, schedules, cfg, fixture_schedules)
            return 0
        providers = [s.collector.provider for s in schedules] + [
            fs.provider for fs in fixture_schedules
        ]
        print(f"scheduler up: {providers} (tick {TICK_SECONDS}s)")
        while True:
            run_tick(conn, schedules, cfg, fixture_schedules)
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        print("scheduler stopped")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
