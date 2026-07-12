"""Pick explainer agent (CLAUDE.md §11).

Input: a pick plus its exact structured feature payload (pick_features row,
JSON only). Output: a <=3-sentence rationale stored in picks.rationale.

Guardrails, all enforced in code (not just in the prompt):
- the prompt forbids inventing numbers; before save, every number in the
  output is validated against the payload (or its x100 percent form) —
  a rationale that fails validation is dropped, never stored;
- §2.4 banned strings are rejected case-insensitively;
- rationale backfill is the ONLY mutation the picks trigger allows (NULL ->
  value, all other fields locked) — see 20260711180000 migration;
- agents never auto-publish picks; this only annotates already-published rows.

Model: claude-sonnet-4-6 via the Messages API (override: EXPLAINER_MODEL).
Run standalone: uv run python -m agents.explainer
Scheduled: nightly batch inside tools.run_collectors (24h floor).
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from typing import Any

import httpx
import psycopg

from core.config import provider_api_key
from core.db import connect

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-6"

# §2.4 — CI also greps these in web/bot copy; the agent must never emit them.
# This tuple IS the validator's denylist, not public copy, hence the marker.
BANNED_STRINGS = (
    "guaranteed",  # banned-strings-allow
    "sure win",  # banned-strings-allow
    "can't lose",  # banned-strings-allow
    "risk-free profit",  # banned-strings-allow
)

SYSTEM_PROMPT = """You explain betting-analytics picks for an educational audience.
You are given ONE pick as a JSON payload. Write a rationale of AT MOST three sentences.

Hard rules:
- Use ONLY numbers that literally appear in the payload (you may express a
  probability or EV as a percentage, e.g. 0.0431 -> 4.3%). NEVER invent,
  derive, or estimate any other number.
- No profit promises of any kind. Educational, analytical tone.
- Mention the price taken, the fair (no-vig) reference, and the edge.
- Plain text only, no markdown, no preamble."""

LLMCall = Callable[[str, str], str]


def _call_claude(system: str, user: str) -> str:
    resp = httpx.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": provider_api_key("anthropic"),
            "anthropic-version": ANTHROPIC_VERSION,
        },
        json={
            "model": os.environ.get("EXPLAINER_MODEL", DEFAULT_MODEL),
            "max_tokens": 300,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    body = resp.json()
    return "".join(b.get("text", "") for b in body.get("content", []))


def _numeric_values(payload: dict[str, Any]) -> set[float]:
    vals: set[float] = set()
    for v in payload.values():
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, int | float):
            vals.add(float(v))
            vals.add(float(v) * 100.0)  # percent form of probabilities/EV
        elif isinstance(v, str):
            try:
                f = float(v)
            except ValueError:
                continue
            vals.add(f)
            vals.add(f * 100.0)
    return vals


_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DIGIT_WORD_RE = re.compile(r"\S*\d\S*")


def _digit_words(payload: dict[str, Any]) -> set[str]:
    """Digit-bearing words from payload strings ('bet365', 'G2 Esports',
    timestamps). These are names, not numbers — the validator must not read
    the 365 in bet365 as an invented figure."""
    words: set[str] = set()
    for v in payload.values():
        if not isinstance(v, str):
            continue
        try:
            float(v)
        except ValueError:
            words.update(_DIGIT_WORD_RE.findall(v))
    return words


def validate_rationale(text: str, payload: dict[str, Any]) -> bool:
    """True iff the text is clean of banned strings and every number in it
    matches a payload value (or its x100 form) after rounding."""
    lowered = text.lower()
    if any(b in lowered for b in BANNED_STRINGS):
        return False
    for word in sorted(_digit_words(payload), key=len, reverse=True):
        text = re.sub(re.escape(word), " ", text, flags=re.IGNORECASE)
    allowed = _numeric_values(payload)
    for tok in _NUM_RE.findall(text):
        num = float(tok)
        decimals = len(tok.split(".")[1]) if "." in tok else 0
        tol = 0.5 * 10.0**-decimals  # rounding tolerance at stated precision
        if not any(abs(num - a) <= tol + 1e-9 for a in allowed):
            return False
    return True


def _pending(conn: psycopg.Connection, limit: int) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select p.id, pf.payload,
                   coalesce(th.canonical_name, e.home_name_raw) as home,
                   coalesce(ta.canonical_name, e.away_name_raw) as away,
                   e.sport_id
            from picks p
            join pick_features pf on pf.pick_id = p.id
            join events e on e.id = p.event_id
            left join teams th on th.id = e.home_team
            left join teams ta on ta.id = e.away_team
            where p.rationale is null
            order by p.published_at
            limit %s
            """,
            (limit,),
        )
        return cur.fetchall()


def explain_pending(
    conn: psycopg.Connection,
    llm: LLMCall | None = None,
    limit: int = 25,
) -> int:
    """Generate + validate + store rationales for picks that lack one."""
    call = llm or _call_claude
    written = 0
    for pick_id, payload, home, away, sport_id in _pending(conn, limit):
        context = {"match": f"{home} vs {away}", "sport": sport_id, **payload}
        user = json.dumps(context, default=str)
        try:
            text = call(SYSTEM_PROMPT, user).strip()
        except Exception as exc:  # noqa: BLE001 — batch survives API hiccups
            print(f"explainer: pick {pick_id} failed: {exc!r}", file=sys.stderr)
            continue
        # validate against the exact context the model saw: payload numbers
        # plus name words like 'G2'/'bet365' that carry digits but aren't data
        if not text or not validate_rationale(text, context):
            print(f"explainer: pick {pick_id} rationale rejected by validator", file=sys.stderr)
            continue
        with conn.cursor() as cur:
            cur.execute(
                "update picks set rationale = %s where id = %s and rationale is null",
                (text, pick_id),
            )
            written += cur.rowcount
    return written


def main() -> None:
    with connect() as conn:
        n = explain_pending(conn)
        conn.commit()
    print(f"explainer: {n} rationales stored")


if __name__ == "__main__":
    main()
