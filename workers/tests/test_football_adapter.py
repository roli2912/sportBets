"""Pin the API-Football adapter parsers to captured sample payloads (§14)."""

import json
from datetime import UTC, datetime
from pathlib import Path

from adapters.football import parse_fixture, parse_result

SAMPLES = Path(__file__).parents[2] / "docs" / "providers" / "samples"


def _load(name: str) -> list[dict]:
    return json.loads((SAMPLES / name).read_text())["response"]


def test_parse_fixture_from_sample():
    items = _load("api_football_fixtures_liga1.json")
    fx = parse_fixture(items[0])
    assert fx.provider == "api_football"
    assert fx.provider_key == "1565186"
    assert fx.sport_id == "football"
    assert fx.competition_name == "Liga I"
    assert fx.home_name == "FC Voluntari"
    assert fx.away_name == "FC Botosani"
    assert fx.commence_time == datetime(2026, 7, 17, 15, 30, tzinfo=UTC)


def test_parse_result_uses_90_minute_score():
    items = {i["fixture"]["id"]: i for i in _load("api_football_results_liga1.json")}

    ft = parse_result(items[1545548])  # FT: Voluntari 3-0 Hermannstadt
    assert (ft.home_score, ft.away_score, ft.raw_status) == (3, 0, "FT")

    # AET: goals say 1-2 after extra time, but 1X2 settles on the 90' 1-1.
    aet = parse_result(items[1546371])
    assert (aet.home_score, aet.away_score, aet.raw_status) == (1, 1, "AET")

    # PEN: shootout 4-2 must not leak into the settled score (90' was 1-1).
    pen = parse_result(items[1545547])
    assert (pen.home_score, pen.away_score, pen.raw_status) == (1, 1, "PEN")


def test_parse_result_skips_unfinished_fixtures():
    upcoming = _load("api_football_fixtures_liga1.json")[0]  # status NS
    assert parse_result(upcoming) is None
