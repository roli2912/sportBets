"""Review-queue CLI — the human side of the entity resolver (CLAUDE.md §7).

Usage:
    uv run python -m tools.review list
    uv run python -m tools.review teams [sport_id]
    uv run python -m tools.review approve <item-id>            # new_team: create
                                                               # ambiguous: link candidate
    uv run python -m tools.review approve <item-id> --team <team-uuid>
    uv run python -m tools.review approve <item-id> --name "Canonical Name"
    uv run python -m tools.review reject <item-id>

Approving stores a VERIFIED alias and immediately re-runs event linking +
cross-provider merge, so blocked events unblock in the same command.
"""

from __future__ import annotations

import sys
from uuid import UUID

import psycopg

from core.db import connect
from resolver.resolver import merge_duplicate_events, resolve_events


def _pending(conn: psycopg.Connection) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select r.id, r.kind, r.provider, r.raw_name, r.sport_id,
                   r.confidence, t.canonical_name
            from review_items r
            left join teams t on t.id = r.candidate_team
            where r.status = 'pending'
            order by r.created_at
            """
        )
        return cur.fetchall()


def cmd_list(conn: psycopg.Connection) -> int:
    rows = _pending(conn)
    if not rows:
        print("review queue is empty")
        return 0
    for rid, kind, provider, raw, sport, conf, cand in rows:
        extra = f" candidate={cand!r} score={float(conf):.2f}" if cand else ""
        print(f"{rid}  [{kind}] {provider}/{sport}: {raw!r}{extra}")
    print(f"\n{len(rows)} pending")
    return 0


def cmd_teams(conn: psycopg.Connection, sport_id: str | None) -> int:
    with conn.cursor() as cur:
        if sport_id:
            cur.execute(
                "select id, sport_id, canonical_name from teams where sport_id = %s"
                " order by canonical_name",
                (sport_id,),
            )
        else:
            cur.execute("select id, sport_id, canonical_name from teams order by canonical_name")
        for tid, sport, name in cur.fetchall():
            print(f"{tid}  {sport:10} {name}")
    return 0


def _get_item(conn: psycopg.Connection, item_id: UUID) -> tuple:
    with conn.cursor() as cur:
        cur.execute(
            """
            select kind, provider, provider_key, raw_name, sport_id, candidate_team, confidence
            from review_items where id = %s and status = 'pending'
            """,
            (item_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise SystemExit(f"no pending review item {item_id}")
    return row


def cmd_approve(
    conn: psycopg.Connection,
    item_id: UUID,
    team_id: UUID | None,
    new_name: str | None,
) -> int:
    kind, provider, provider_key, raw_name, sport_id, candidate, confidence = _get_item(
        conn, item_id
    )
    with conn.cursor() as cur:
        if team_id is None and new_name is None and kind == "ambiguous_alias":
            team_id = candidate  # human confirmed the fuzzy candidate
        if team_id is None:
            canonical = new_name or raw_name
            cur.execute(
                "insert into teams (sport_id, canonical_name) values (%s, %s) returning id",
                (sport_id, canonical),
            )
            team_id = cur.fetchone()[0]  # type: ignore[index]
            print(f"created team {team_id} {canonical!r} ({sport_id})")
        cur.execute(
            """
            insert into team_aliases (team_id, provider, provider_key, alias, confidence, verified)
            values (%s, %s, %s, %s, %s, true)
            on conflict (provider, alias)
            do update set team_id = excluded.team_id, verified = true
            """,
            (team_id, provider, provider_key, raw_name, confidence or 1.0),
        )
        cur.execute(
            "update review_items set status = 'approved', resolved_at = now() where id = %s",
            (item_id,),
        )
    linked = resolve_events(conn)
    merged = merge_duplicate_events(conn)
    conn.commit()
    print(
        f"approved {raw_name!r} -> team {team_id}; "
        f"events linked: {linked['events_linked']}, merged: {merged}"
    )
    return 0


def cmd_reject(conn: psycopg.Connection, item_id: UUID) -> int:
    _get_item(conn, item_id)
    with conn.cursor() as cur:
        cur.execute(
            "update review_items set status = 'rejected', resolved_at = now() where id = %s",
            (item_id,),
        )
    conn.commit()
    print(f"rejected {item_id}")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, *rest = argv
    conn = connect()
    try:
        if cmd == "list":
            return cmd_list(conn)
        if cmd == "teams":
            return cmd_teams(conn, rest[0] if rest else None)
        if cmd == "approve":
            item_id = UUID(rest[0])
            team_id = UUID(rest[rest.index("--team") + 1]) if "--team" in rest else None
            new_name = rest[rest.index("--name") + 1] if "--name" in rest else None
            return cmd_approve(conn, item_id, team_id, new_name)
        if cmd == "reject":
            return cmd_reject(conn, UUID(rest[0]))
        print(__doc__)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
