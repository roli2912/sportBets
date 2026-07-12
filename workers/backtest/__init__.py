"""Backtest harness (CLAUDE.md §10) — walk-forward only, leakage-audited.

Gate prerequisite: no Layer-2 model enters `shadow` without a harness run
(§9 research -> shadow). See backtest.harness for the rules enforcement and
backtest.smoke for the end-to-end smoke on captured Layer-1 snapshots.
"""
