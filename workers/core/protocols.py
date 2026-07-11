"""Plug-in contracts (CLAUDE.md §7).

These Protocols are what make sports plug-ins: adding a sport means one new
SportAdapter + one new FeatureModule + model entries. Do NOT add sport-specific
branches outside adapter/feature modules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from core.types import FeatureVector, Fixture, OddsSnapshot, Result


class SportAdapter(Protocol):
    sport_id: str

    def fixtures(self, since: datetime, until: datetime) -> list[Fixture]: ...

    def results(self, since: datetime) -> list[Result]: ...

    def stats(self, event_id: str) -> dict:
        """Raw provider stats, normalized keys."""
        ...


class FeatureModule(Protocol):
    sport_id: str

    def features(self, event_id: str, as_of: datetime) -> FeatureVector:
        """MUST only use data timestamped strictly before `as_of`.
        The backtest harness audits this — CLAUDE.md §10."""
        ...


class Model(Protocol):
    model_id: str

    def predict(self, fv: FeatureVector) -> dict[str, float]:
        """outcome -> probability; probabilities sum to 1 within a market."""
        ...


class OddsCollector(Protocol):
    """One implementation per odds provider (therundown, oddspapi, ...).

    `poll` returns fixtures AND snapshots from one provider pass — both real
    providers (TheRundown v2 events, OddsPapi odds-by-tournaments) deliver
    schedule and prices in the same response, so splitting them would double
    the request cost."""

    provider: str

    def poll(
        self, since: datetime, until: datetime
    ) -> tuple[list[Fixture], list[OddsSnapshot]]: ...
