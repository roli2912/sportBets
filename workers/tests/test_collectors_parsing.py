"""Parser tests pinned to captured raw payloads in docs/providers/samples/.

CLAUDE.md §14: never invent provider response fields — these tests are the
executable proof that parsers match what the providers actually send.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from collectors.oddspapi import OddsPapiCollector, select_active_tournaments
from collectors.therundown import TheRundownCollector, american_to_decimal

SAMPLES = Path(__file__).resolve().parents[2] / "docs" / "providers" / "samples"


def _load(name: str) -> dict | list:
    return json.loads((SAMPLES / name).read_text())


# -- TheRundown ---------------------------------------------------------------


def test_american_to_decimal() -> None:
    assert american_to_decimal(-105) == pytest.approx(1.95238, abs=1e-5)
    assert american_to_decimal(270) == pytest.approx(3.70)
    assert american_to_decimal(100) == pytest.approx(2.0)
    with pytest.raises(ValueError, match="invalid"):
        american_to_decimal(0)


@pytest.fixture
def therundown() -> TheRundownCollector:
    return TheRundownCollector(
        api_key="test",
        affiliates={19: "draftkings", 22: "betmgm", 23: "fanduel"},
    )


@pytest.fixture
def tr_event() -> dict:
    return _load("therundown_events_fifa.json")["events"][0]


def test_therundown_parse_fixture(therundown: TheRundownCollector, tr_event: dict) -> None:
    fx = therundown.parse_fixture(tr_event)
    assert fx is not None
    assert fx.provider == "therundown"
    assert fx.provider_key == tr_event["event_id"]
    assert fx.sport_id == "football"
    assert fx.home_name == "Norway"  # is_home, not list order
    assert fx.away_name == "England"
    assert fx.commence_time == datetime(2026, 7, 11, 21, 0, tzinfo=UTC)


def test_therundown_parse_snapshots(therundown: TheRundownCollector, tr_event: dict) -> None:
    snaps = therundown.parse_snapshots(tr_event)
    assert snaps, "sample event has priced markets"
    assert {s.market for s in snaps} <= {"h2h", "asian_handicap", "totals"}
    assert all(s.price > 1.0 for s in snaps)  # decimal odds at the boundary
    assert all(s.bookmaker in {"draftkings", "betmgm", "fanduel"} for s in snaps)

    h2h = [s for s in snaps if s.market == "h2h"]
    assert {s.outcome for s in h2h} == {"home", "away", "draw"}
    # England (TYPE_TEAM, is_away) at BetMGM: -105 american
    away_betmgm = next(s for s in h2h if s.outcome == "away" and s.bookmaker == "betmgm")
    assert away_betmgm.price == pytest.approx(american_to_decimal(-105))
    assert away_betmgm.line is None

    ah = [s for s in snaps if s.market == "asian_handicap"]
    assert ah and all(s.line is not None for s in ah)
    assert {s.outcome for s in ah} == {"home", "away"}

    # main_lines_only=True must drop non-main total lines (e.g. 0.5 with
    # is_main_line=false in the sample)
    totals = [s for s in snaps if s.market == "totals"]
    assert all(s.line != 0.5 for s in totals)


def test_therundown_skips_unknown_sport(therundown: TheRundownCollector, tr_event: dict) -> None:
    event = {**tr_event, "sport_id": 999999}
    assert therundown.parse_fixture(event) is None


# -- OddsPapi -------------------------------------------------------------------


@pytest.fixture
def oddspapi() -> OddsPapiCollector:
    return OddsPapiCollector(
        tournament_ids=[31621],
        api_key="test",
        markets_ref=_load("oddspapi_markets.json"),
    )


def test_oddspapi_parse_fixture(oddspapi: OddsPapiCollector) -> None:
    raw = _load("oddspapi_fixtures_cs2_blast.json")[0]
    fx = oddspapi.parse_fixture(raw)
    assert fx is not None
    assert fx.provider == "oddspapi"
    assert fx.provider_key == raw["fixtureId"]
    assert fx.sport_id == "cs2"
    assert fx.home_name == "Flyquest"
    assert fx.away_name == "Mibr"
    assert fx.commence_time.tzinfo is not None


def test_oddspapi_parse_snapshots(oddspapi: OddsPapiCollector) -> None:
    raw = _load("oddspapi_odds_cs2_blast.json")[0]
    snaps = oddspapi.parse_snapshots(raw)

    # Full-match markets only: per-map moneylines (period p1..p3) are skipped.
    assert {s.market for s in snaps} == {"h2h", "total_maps", "map_handicap"}
    assert len(snaps) == 6
    assert all(s.bookmaker == "pinnacle" for s in snaps)
    assert all(s.event_provider_key == raw["fixtureId"] for s in snaps)

    by = {(s.market, s.outcome): s for s in snaps}
    assert by[("h2h", "home")].price == pytest.approx(1.591)  # decimal, no conversion
    assert by[("h2h", "away")].price == pytest.approx(2.25)
    assert by[("h2h", "home")].line is None

    assert by[("total_maps", "over")].price == pytest.approx(2.01)
    assert by[("total_maps", "over")].line == pytest.approx(2.5)  # from /markets ref
    assert by[("total_maps", "under")].line == pytest.approx(2.5)

    assert by[("map_handicap", "home")].line == pytest.approx(-1.5)
    assert by[("map_handicap", "away")].price == pytest.approx(1.418)


def test_oddspapi_skips_suspended_bookmaker(oddspapi: OddsPapiCollector) -> None:
    raw = json.loads(json.dumps(_load("oddspapi_odds_cs2_blast.json")[0]))
    raw["bookmakerOdds"]["pinnacle"]["suspended"] = True
    assert oddspapi.parse_snapshots(raw) == []


def test_oddspapi_select_active_tournaments_from_sample() -> None:
    """Discovery picks the busiest tournaments by live counts (a pinned id
    404s the moment its event ends — observed 2026-07-12 with BLAST 31621)."""
    rows = _load("oddspapi_tournaments_cs2.json")
    # sample top counts: 48529 (10 upcoming/future), 50796 (8)
    assert select_active_tournaments(rows, limit=2) == [48529, 50796]
    # the cap protects the ~250 req/mo budget
    assert len(select_active_tournaments(rows, limit=1)) == 1
    # zero-count tournaments are never selected, even under the cap
    assert 31621 in select_active_tournaments(rows, limit=10)
    quiet = [{"tournamentId": 1, "liveFixtures": 0}, {"tournamentId": 2}]
    assert select_active_tournaments(quiet, limit=2) == []
