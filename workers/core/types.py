"""Domain types shared across workers.

All timestamps are UTC (CLAUDE.md §14). Types are provider-agnostic; provider
payload parsing lives in the collector/adapter that owns it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator


class Fixture(BaseModel):
    """A scheduled match as reported by one provider (pre entity-resolution)."""

    provider: str
    provider_key: str
    sport_id: str
    competition_name: str | None = None
    home_name: str
    away_name: str
    commence_time: datetime

    @field_validator("commence_time")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("commence_time must be timezone-aware (UTC)")
        return v.astimezone(UTC)


class Result(BaseModel):
    """A finished match result as reported by one provider."""

    provider: str
    provider_key: str
    sport_id: str
    home_score: int
    away_score: int
    finished_at: datetime | None = None
    raw_status: str | None = None


class FeatureVector(BaseModel):
    """Feature payload for a model. `as_of` is the leakage boundary:
    every feature MUST be derived only from data timestamped strictly
    before `as_of` (CLAUDE.md §7, §10)."""

    sport_id: str
    event_id: str
    as_of: datetime
    features: dict[str, float | int | str | None] = Field(default_factory=dict)


class OddsSnapshot(BaseModel):
    """One price observation for one outcome at one bookmaker."""

    provider: str
    event_provider_key: str
    bookmaker: str
    market: str  # canonical key from packages/shared/markets.json
    outcome: str
    price: float  # decimal odds
    line: float | None = None
    captured_at: datetime

    @field_validator("price")
    @classmethod
    def _valid_decimal_odds(cls, v: float) -> float:
        if v <= 1.0:
            raise ValueError(f"decimal odds must be > 1.0, got {v}")
        return v

    @field_validator("captured_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware (UTC)")
        return v.astimezone(UTC)
