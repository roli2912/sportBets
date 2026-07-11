"""Scheduler math: §8 cadence bands floored by provider budget."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tools.run_collectors import DISCOVERY_INTERVAL, effective_interval, is_due

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def test_no_known_events_uses_discovery_interval() -> None:
    assert effective_interval(None, NOW, timedelta(hours=1)) == DISCOVERY_INTERVAL


def test_cadence_band_applies_when_above_budget_floor() -> None:
    kickoff = NOW + timedelta(hours=72)  # >48h band -> 1/day
    assert effective_interval(kickoff, NOW, timedelta(hours=1)) == timedelta(days=1)


def test_budget_floor_overrides_cadence_near_kickoff() -> None:
    kickoff = NOW + timedelta(minutes=30)  # 2h-15m band -> every 5 min
    assert effective_interval(kickoff, NOW, timedelta(hours=12)) == timedelta(hours=12)


def test_post_kickoff_falls_back_to_tightest_band() -> None:
    kickoff = NOW - timedelta(minutes=1)  # poll_interval -> None once kicked off
    assert effective_interval(kickoff, NOW, timedelta(minutes=1)) == timedelta(minutes=2)


def test_is_due_first_run_and_elapsed() -> None:
    kickoff = NOW + timedelta(hours=12)  # 24h-2h band -> hourly
    floor = timedelta(minutes=30)
    assert is_due(None, kickoff, NOW, floor)  # never polled -> due
    assert not is_due(NOW - timedelta(minutes=59), kickoff, NOW, floor)
    assert is_due(NOW - timedelta(hours=1), kickoff, NOW, floor)
