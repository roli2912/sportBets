"""Environment-driven configuration.

Secrets live in .env (never committed). Provider keys follow the
{PROVIDER}_API_KEY convention (CLAUDE.md §14). Thresholds are config, not code
(CLAUDE.md §8).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


class MissingConfigError(RuntimeError):
    pass


def provider_api_key(provider: str) -> str:
    """Return the API key for a provider, e.g. provider_api_key('therundown')
    reads THERUNDOWN_API_KEY."""
    var = f"{provider.upper()}_API_KEY"
    value = os.environ.get(var, "").strip()
    if not value:
        raise MissingConfigError(f"{var} is not set (see .env.example)")
    return value


def database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise MissingConfigError("DATABASE_URL is not set (see .env.example)")
    return value


@dataclass(frozen=True)
class EngineConfig:
    """Layer-1 publish parameters (CLAUDE.md §8). Config, not code."""

    ev_threshold: float = 0.025  # publish when EV >= 2.5% after haircut
    margin_haircut: float = 0.005  # conservative shave applied to raw EV
    kelly_fraction: float = 0.25  # ALWAYS fractional; never publish full Kelly
    max_stake_units: float = 2.0  # hard cap per pick
    bankroll_units: float = 100.0  # 1 unit == 1% of bankroll

    @staticmethod
    def from_env() -> EngineConfig:
        def _f(name: str, default: float) -> float:
            raw = os.environ.get(name)
            return float(raw) if raw else default

        return EngineConfig(
            ev_threshold=_f("ENGINE_EV_THRESHOLD", 0.025),
            margin_haircut=_f("ENGINE_MARGIN_HAIRCUT", 0.005),
            kelly_fraction=_f("ENGINE_KELLY_FRACTION", 0.25),
            max_stake_units=_f("ENGINE_MAX_STAKE_UNITS", 2.0),
            bankroll_units=_f("ENGINE_BANKROLL_UNITS", 100.0),
        )


@dataclass(frozen=True)
class Settings:
    engine: EngineConfig = field(default_factory=EngineConfig.from_env)
