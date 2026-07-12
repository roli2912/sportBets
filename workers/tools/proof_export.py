"""Nightly proof export (§6 proof layer; §13 Task 4).

Appends picks and settlements as JSONL to a directory meant to be a PUBLIC
append-only GitHub repo. Existing lines are never rewritten or reordered —
any history rewrite would be publicly visible in the repo's git history,
which is the entire point of the proof layer.

Idempotent (§14): rows already present (by pick_id) are skipped; a re-run
appends nothing and leaves the files byte-identical.

Run: uv run python -m tools.proof_export [--out DIR] [--git]
Env: PROOF_EXPORT_DIR (default ./proof-export), PROOF_EXPORT_GIT=1 to
commit+push when DIR is a git repo.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import psycopg

from core.db import connect

PICKS_FILE = "picks.jsonl"
SETTLEMENTS_FILE = "settlements.jsonl"


def _existing_ids(path: Path, key: str) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text().splitlines():
        if line.strip():
            ids.add(str(json.loads(line)[key]))
    return ids


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    with path.open("a") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return len(rows)


def export_proof(conn: psycopg.Connection, out_dir: str | Path) -> dict[str, int]:
    """Append all not-yet-exported picks and settlements. Deterministic
    order (published_at/settled_at, then id) so exports are reproducible."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    picks_path = out / PICKS_FILE
    settlements_path = out / SETTLEMENTS_FILE

    have_picks = _existing_ids(picks_path, "pick_id")
    with conn.cursor() as cur:
        cur.execute(
            """
            select p.id, p.model_id, e.sport_id, p.event_id, e.commence_time,
                   coalesce(th.canonical_name, '?') || ' – ' ||
                   coalesce(ta.canonical_name, '?') as match,
                   p.market, p.outcome, p.line, p.price_at_publish,
                   p.bookmaker, p.stake_units, p.published_at, p.features_hash
            from picks p
            join events e on e.id = p.event_id
            left join teams th on th.id = e.home_team
            left join teams ta on ta.id = e.away_team
            order by p.published_at, p.id
            """
        )
        new_picks = [
            {
                "pick_id": str(r[0]),
                "model_id": r[1],
                "sport_id": r[2],
                "event_id": str(r[3]),
                "commence_time": r[4].isoformat(),
                "match": r[5],
                "market": r[6],
                "outcome": r[7],
                "line": float(r[8]) if r[8] is not None else None,
                "price_at_publish": float(r[9]),
                "bookmaker": r[10],
                "stake_units": float(r[11]),
                "published_at": r[12].isoformat(),
                "features_hash": r[13],
            }
            for r in cur.fetchall()
            if str(r[0]) not in have_picks
        ]

    have_settlements = _existing_ids(settlements_path, "pick_id")
    with conn.cursor() as cur:
        cur.execute(
            """
            select pick_id, result, closing_price, clv, pnl_units, settled_at
            from settlements
            order by settled_at, pick_id
            """
        )
        new_settlements = [
            {
                "pick_id": str(r[0]),
                "result": r[1],
                "closing_price": float(r[2]) if r[2] is not None else None,
                "clv": float(r[3]) if r[3] is not None else None,
                "pnl_units": float(r[4]),
                "settled_at": r[5].isoformat(),
            }
            for r in cur.fetchall()
            if str(r[0]) not in have_settlements
        ]

    return {
        "picks": _append_jsonl(picks_path, new_picks),
        "settlements": _append_jsonl(settlements_path, new_settlements),
    }


def git_publish(out_dir: str | Path) -> bool:
    """Commit + push the export if out_dir is a git repo with changes."""
    out = Path(out_dir)
    if not (out / ".git").exists():
        return False
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=out, text=True).strip()
    if not status:
        return False
    subprocess.check_call(["git", "add", "-A"], cwd=out)
    subprocess.check_call(["git", "commit", "-m", "proof export (append-only)"], cwd=out)
    subprocess.check_call(["git", "push"], cwd=out)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.environ.get("PROOF_EXPORT_DIR", "proof-export"))
    ap.add_argument("--git", action="store_true", help="commit+push if --out is a git repo")
    args = ap.parse_args()

    with connect() as conn:
        counts = export_proof(conn, args.out)
    pushed = git_publish(args.out) if args.git else False
    print(
        f"proof export: +{counts['picks']} picks, +{counts['settlements']} settlements "
        f"-> {args.out}{' (pushed)' if pushed else ''}"
    )


if __name__ == "__main__":
    main()
