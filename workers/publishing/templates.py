"""Message templates for outbound publishing (Telegram/Discord).

Every public surface carries the 18+ / responsible-gambling line (CLAUDE.md
§2.5) and no profit-guarantee language (§2.4 — this directory is covered by
scripts/check_banned_strings.sh). Templates only format data that is already
on the immutable pick row; they never invent numbers (§11).
"""

from __future__ import annotations

from datetime import datetime

# One line, appended to every outbound message. §2.5 is non-negotiable.
COMPLIANCE_LINE = (
    "18+ | Educational research, not betting advice — outcomes are always "
    "uncertain. Bet responsibly: begambleaware.org | gamblingtherapy.org | "
    "jocresponsabil.ro"
)

# §9 (as amended): the label split is part of the product's honesty.
STATUS_LABELS = {
    "public": "VERIFIED model pick",
    "shadow": "Market-edge signal (validation ongoing)",
}


def _label(model_status: str) -> str:
    return STATUS_LABELS.get(model_status, STATUS_LABELS["shadow"])


def format_pick(
    *,
    match: str,
    market: str,
    outcome: str,
    price: float,
    bookmaker: str,
    published_at: datetime,
    model_status: str,
    line: float | None = None,
    rationale: str | None = None,
) -> str:
    """One pick -> one Telegram message (plain text, no markdown surprises)."""
    market_txt = f"{market} {line:g}" if line is not None else market
    parts = [
        f"[{_label(model_status)}]",
        f"{match}",
        f"Market: {market_txt} — {outcome}",
        f"Odds: {price:.2f} @ {bookmaker}",
        f"Published: {published_at:%Y-%m-%d %H:%M} UTC",
    ]
    if rationale:
        parts.append(rationale.strip())
    parts.append(COMPLIANCE_LINE)
    return "\n".join(parts)


def format_daily_digest(feed_date: str, entries: list[str]) -> str:
    """Morning digest: pre-formatted per-pick blocks joined under one header."""
    header = f"Daily board — {feed_date}\nEvery pick below is timestamped and graded in public."
    body = "\n\n".join(entries) if entries else "No qualifying picks today — no forcing."
    return f"{header}\n\n{body}\n\n{COMPLIANCE_LINE}"
