"""Backtest harness leakage guards + end-to-end replay (CLAUDE.md §10)."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from backtest.harness import (
    LeakageError,
    SourceRow,
    audit_sources,
    build_artifact,
    run_backtest,
    walk_forward,
    write_artifact,
)
from backtest.smoke import SharpMirrorModel, SharpRefFeatureModule
from core.config import EngineConfig
from core.types import FeatureVector
from tests.conftest import requires_db

T0 = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

# ------------------------------------------------------------- §10.4 audit


def test_audit_passes_when_strictly_before() -> None:
    audit_sources([SourceRow("t", "1", T0 - timedelta(seconds=1))], as_of=T0)


def test_audit_rejects_equal_timestamp() -> None:
    with pytest.raises(LeakageError, match="strictly older"):
        audit_sources([SourceRow("t", "1", T0)], as_of=T0)


def test_audit_rejects_future_row() -> None:
    with pytest.raises(LeakageError):
        audit_sources(
            [
                SourceRow("t", "old", T0 - timedelta(days=1)),
                SourceRow("t", "new", T0 + timedelta(seconds=1)),
            ],
            as_of=T0,
        )


def test_module_without_provenance_is_refused() -> None:
    class NoProvenance:
        sport_id = "cs2"

        def features(self, event_id: str, as_of: datetime) -> FeatureVector:
            return FeatureVector(sport_id="cs2", event_id=event_id, as_of=as_of)

    with pytest.raises(LeakageError, match="last_sources"):
        run_backtest(
            None,  # type: ignore[arg-type] — refused before any DB use
            NoProvenance(),  # type: ignore[arg-type]
            SharpMirrorModel(),
            sport_id="cs2",
            bookmaker="softbook",
            market="h2h",
            start=T0,
            end=T0 + timedelta(days=1),
        )


# ------------------------------------------------------- §10.1 walk-forward


def test_walk_forward_rejects_unsorted() -> None:
    times = [T0, T0 - timedelta(hours=1)]
    with pytest.raises(ValueError, match="chronologically sorted"):
        list(walk_forward(times, n_splits=1))


def test_walk_forward_train_strictly_precedes_test() -> None:
    times = [T0 + timedelta(hours=i) for i in range(10)]
    folds = list(walk_forward(times, n_splits=3, min_train=4))
    assert len(folds) == 3
    covered: list[int] = []
    for train, test in folds:
        assert train == list(range(train[-1] + 1))  # contiguous prefix, no shuffle
        assert max(train) < min(test)  # train strictly before test
        covered += test
    assert covered == list(range(4, 10))  # tail fully covered, in order


def test_walk_forward_rejects_impossible_split() -> None:
    with pytest.raises(ValueError):
        list(walk_forward([T0, T0], n_splits=5, min_train=1))


# --------------------------------------------------------- DB end-to-end


def _seed_finished_event(conn, *, home_score: int = 16, away_score: int = 9):
    """Finished CS2 match with sharp tick, soft-book tick after it, sharp close."""
    now = datetime.now(UTC)
    commence = now - timedelta(hours=2)
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into events (sport_id, commence_time, status, provider_keys)
            values ('cs2', %s, 'finished', %s::jsonb) returning id
            """,
            (commence, json.dumps({"test": str(uuid.uuid4())})),
        )
        event = cur.fetchone()[0]
        cur.execute(
            """
            insert into event_results (event_id, provider, home_score, away_score)
            values (%s, 'test', %s, %s)
            """,
            (event, home_score, away_score),
        )
        rows = [
            # sharp reference tick, 2h before kickoff
            ("pinnacle", "home", 1.80, commence - timedelta(hours=2), False),
            ("pinnacle", "away", 2.05, commence - timedelta(hours=2), False),
            # soft book last pre-match prices, 1h before kickoff (home mispriced)
            ("softbook", "home", 2.10, commence - timedelta(hours=1), False),
            ("softbook", "away", 1.70, commence - timedelta(hours=1), False),
            # sharp close
            ("pinnacle", "home", 1.75, commence, True),
            ("pinnacle", "away", 2.10, commence, True),
        ]
        for bookmaker, outcome, price, captured, closing in rows:
            cur.execute(
                """
                insert into odds_snapshots
                  (event_id, bookmaker, market, outcome, price, captured_at, is_closing)
                values (%s, %s, 'h2h', %s, %s, %s, %s)
                """,
                (event, bookmaker, outcome, price, captured, closing),
            )
    return event, commence


@requires_db
def test_backtest_end_to_end(conn, tmp_path) -> None:
    event, commence = _seed_finished_event(conn)
    now = datetime.now(UTC)
    fm = SharpRefFeatureModule(conn, "cs2")
    model = SharpMirrorModel()
    report = run_backtest(
        conn,
        fm,
        model,
        sport_id="cs2",
        bookmaker="softbook",
        market="h2h",
        start=now - timedelta(days=1),
        end=now,
        cfg=EngineConfig(),
    )
    assert report.n_events >= 1
    picks = [p for p in report.picks if p.event_id == str(event)]
    assert len(picks) == 1  # only the mispriced home side clears the threshold
    p = picks[0]
    assert p.outcome == "home"
    assert p.price == 2.10
    assert p.as_of == commence - timedelta(hours=1)  # decision snapshot, not kickoff
    # sharp novig at decision: 1.80/2.05 -> p_home ~= 0.5325
    assert p.p_model == pytest.approx(0.5325, abs=1e-3)
    assert p.result == "win"
    assert p.pnl_units == pytest.approx(1.10)  # flat 1.0 unit at 2.10
    # close 1.75/2.10 -> novig home close ~= 1.8333; clv = 2.10/1.8333 - 1
    assert p.clv == pytest.approx(0.1455, abs=1e-3)

    artifact = build_artifact(report, config={"model": model.model_id})
    assert (
        artifact["config_hash"]
        == build_artifact(report, config={"model": model.model_id})["config_hash"]
    )
    assert len(artifact["config_hash"]) == 64
    assert artifact["git_sha"] == "unknown" or len(artifact["git_sha"]) == 40
    assert artifact["data_range"]["start"] and artifact["data_range"]["end"]
    assert artifact["n_picks"] == len(report.picks)
    assert artifact["calibration"]  # at least one populated bin
    path = write_artifact(artifact, tmp_path)
    assert path.exists()
    assert json.loads(path.read_text())["model_id"] == model.model_id


@requires_db
def test_feature_module_uses_only_strictly_older_rows(conn) -> None:
    event, commence = _seed_finished_event(conn)
    fm = SharpRefFeatureModule(conn, "cs2")
    as_of = commence - timedelta(hours=1)  # soft-book decision time
    fv = fm.features(str(event), as_of)
    assert set(fv.features) == {"novig_home", "novig_away"}
    for src in fm.last_sources():
        assert src.ingested_at < as_of  # the equal/closing rows are excluded


@requires_db
def test_leakage_injection_raises(conn) -> None:
    _seed_finished_event(conn)

    class LeakyModule:
        sport_id = "cs2"

        def __init__(self) -> None:
            self._as_of: datetime | None = None

        def features(self, event_id: str, as_of: datetime) -> FeatureVector:
            self._as_of = as_of
            return FeatureVector(
                sport_id="cs2",
                event_id=event_id,
                as_of=as_of,
                features={"novig_home": 0.6, "novig_away": 0.4},
            )

        def last_sources(self) -> list[SourceRow]:
            assert self._as_of is not None
            return [SourceRow("event_results", "leak", self._as_of)]  # == as_of

    now = datetime.now(UTC)
    with pytest.raises(LeakageError, match="strictly older"):
        run_backtest(
            conn,
            LeakyModule(),
            SharpMirrorModel(),
            sport_id="cs2",
            bookmaker="softbook",
            market="h2h",
            start=now - timedelta(days=1),
            end=now,
        )
