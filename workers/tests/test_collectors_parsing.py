"""Parser tests pinned to captured raw payloads in docs/providers/samples/.

CLAUDE.md §14: never invent provider response fields — these tests are the
executable proof that parsers match what the providers actually send.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adapters.cs2 import PandaScoreAdapter, _commence_time, parse_fixture, parse_result
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


# -- PandaScore (CS2 stats side) ----------------------------------------------


@pytest.fixture
def ps_upcoming() -> list[dict]:
    return _load("pandascore_matches_upcoming_cs2.json")


@pytest.fixture
def ps_past() -> list[dict]:
    return _load("pandascore_matches_past_cs2.json")


def test_pandascore_parse_fixture(ps_upcoming: list[dict]) -> None:
    fx = parse_fixture(ps_upcoming[0])
    assert fx is not None
    assert fx.provider == "pandascore"
    assert fx.provider_key == "1577290"
    assert fx.sport_id == "cs2"
    assert fx.competition_name == "Thunderpick World Championship"
    # Esports has no home/away — these are just opponents[0]/[1] in
    # PandaScore's own order (docs/providers/pandascore.md).
    assert fx.home_name == "largadosypelados"
    assert fx.away_name == "Imperial"
    assert fx.commence_time == datetime(2026, 7, 12, 21, 0, tzinfo=UTC)


def test_pandascore_fixture_skips_tbd_opponents(
    ps_upcoming: list[dict], ps_past: list[dict]
) -> None:
    """Bracket slots not yet settled -> fewer than 2 opponents -> skip."""
    tbd = {m["id"]: m for m in ps_upcoming + ps_past if len(m.get("opponents", [])) != 2}
    assert tbd, "samples contain TBD rows (ids 1577291, 1582130 at capture time)"
    for match in tbd.values():
        assert parse_fixture(match) is None
        assert parse_result(match) is None


def test_pandascore_parse_result_scores_by_team_id(ps_past: list[dict]) -> None:
    """Scores come from results[].team_id mapping, never list position, and the
    Result carries the provider's raw names so persist_results can align
    orientation after cross-provider merges."""
    res = parse_result(ps_past[0])
    assert res is not None
    assert res.provider == "pandascore"
    assert res.provider_key == "1200600"
    assert res.sport_id == "cs2"
    # opponents: (135743, BESTIA Academy), (135505, RED Canids Academy)
    # results:   {135743: 0}, {135505: 2}
    assert res.home_name == "BESTIA Academy"
    assert res.away_name == "RED Canids Academy"
    assert (res.home_score, res.away_score) == (0, 2)
    assert res.raw_status == "finished"
    assert res.finished_at is None  # all timestamps null on this real row


def test_pandascore_result_requires_finished(ps_upcoming: list[dict]) -> None:
    assert ps_upcoming[0]["status"] == "not_started"
    assert parse_result(ps_upcoming[0]) is None


def test_pandascore_commence_time_fallback_chain() -> None:
    assert _commence_time({"begin_at": "2026-07-12T21:00:00Z"}) == datetime(
        2026, 7, 12, 21, 0, tzinfo=UTC
    )
    assert _commence_time({"begin_at": None, "scheduled_at": "2026-07-12T22:00:00Z"}) == datetime(
        2026, 7, 12, 22, 0, tzinfo=UTC
    )
    assert _commence_time(
        {"begin_at": None, "scheduled_at": None, "original_scheduled_at": "2026-07-12T23:00:00Z"}
    ) == datetime(2026, 7, 12, 23, 0, tzinfo=UTC)
    # observed on a real finished row (id 1200600): all three null
    assert (
        _commence_time({"begin_at": None, "scheduled_at": None, "original_scheduled_at": None})
        is None
    )


class _FakePandaClient:
    """Stands in for httpx.Client — replays the captured sample pages."""

    def __init__(self, pages: dict[str, list[dict]]) -> None:
        self._pages = pages
        self.calls: list[str] = []

    def get(self, path: str, params: dict | None = None):  # noqa: ANN201 - test stub
        self.calls.append(path)
        import httpx

        return httpx.Response(
            200,
            json=self._pages[path],
            request=httpx.Request("GET", f"https://api.pandascore.co{path}"),
        )


def test_pandascore_results_keeps_timestampless_rows(ps_past: list[dict]) -> None:
    """A finished match with ALL timestamps null cannot be window-filtered —
    it must still be returned (persist_results only attaches known events)."""
    client = _FakePandaClient({"/csgo/matches/past": ps_past})
    adapter = PandaScoreAdapter(client=client, api_key="test")  # type: ignore[arg-type]
    # `since` far in the future: only timestamp-less rows can survive.
    results = adapter.results(since=datetime(2030, 1, 1, tzinfo=UTC))
    assert [r.provider_key for r in results] == ["1200600"]


def test_pandascore_results_window_filter(ps_past: list[dict]) -> None:
    client = _FakePandaClient({"/csgo/matches/past": ps_past})
    adapter = PandaScoreAdapter(client=client, api_key="test")  # type: ignore[arg-type]
    results = adapter.results(since=datetime(2020, 1, 1, tzinfo=UTC))
    keys = {r.provider_key for r in results}
    assert "1200600" in keys  # timestamp-less row kept
    assert "1582113" in keys  # begin_at 2026-07-12 >= since


def test_pandascore_fixtures_window_filter(ps_upcoming: list[dict]) -> None:
    client = _FakePandaClient({"/csgo/matches/upcoming": ps_upcoming})
    adapter = PandaScoreAdapter(client=client, api_key="test")  # type: ignore[arg-type]
    window = adapter.fixtures(
        since=datetime(2026, 7, 12, tzinfo=UTC), until=datetime(2026, 7, 13, tzinfo=UTC)
    )
    assert any(fx.provider_key == "1577290" for fx in window)
    assert all(
        datetime(2026, 7, 12, tzinfo=UTC) <= fx.commence_time <= datetime(2026, 7, 13, tzinfo=UTC)
        for fx in window
    )
    nothing = adapter.fixtures(
        since=datetime(2030, 1, 1, tzinfo=UTC), until=datetime(2030, 1, 2, tzinfo=UTC)
    )
    assert nothing == []
