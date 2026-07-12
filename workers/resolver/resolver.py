"""Entity resolver (CLAUDE.md §7): raw provider names -> canonical team ids.

Rules (pre-registered in CLAUDE.md, do not weaken):
- Exact match on (provider, provider_key) or a stored alias -> return id.
- Else fuzzy (rapidfuzz token_set_ratio) vs canonical names + aliases:
    score >= 92  auto-link, store alias (verified=false)
    80 - 92      review queue; dependent rows stay unlinked until resolved
    < 80         review item as probable-new-team
- Cross-provider events: same sport + same resolved team pair + commence_time
  within +/-36h -> merged into one row, provider_keys unioned.
- Never guess silently. Unresolved entities block visibly, not wrongly.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg
from rapidfuzz import fuzz

AUTO_LINK_SCORE = 92.0
REVIEW_MIN_SCORE = 80.0
EVENT_MERGE_WINDOW_HOURS = 36


@dataclass(frozen=True)
class ResolveOutcome:
    """What happened for one raw name. team_id is None unless resolved."""

    team_id: UUID | None
    action: str  # 'alias' | 'auto_linked' | 'queued_ambiguous' | 'queued_new_team'
    score: float | None = None


def _alias_match(
    conn: psycopg.Connection, provider: str, provider_key: str | None, raw_name: str
) -> UUID | None:
    with conn.cursor() as cur:
        cur.execute(
            "select team_id from team_aliases where provider = %s and lower(alias) = lower(%s)",
            (provider, raw_name),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        if provider_key:
            cur.execute(
                "select team_id from team_aliases where provider = %s and provider_key = %s",
                (provider, provider_key),
            )
            row = cur.fetchone()
            if row:
                return row[0]
    return None


def _candidates(conn: psycopg.Connection, sport_id: str) -> list[tuple[UUID, str]]:
    """(team_id, name) pairs: canonical names plus every known alias."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select id, canonical_name from teams where sport_id = %s
            union
            select a.team_id, a.alias
            from team_aliases a join teams t on t.id = a.team_id
            where t.sport_id = %s
            """,
            (sport_id, sport_id),
        )
        return list(cur.fetchall())


def _store_alias(
    conn: psycopg.Connection,
    *,
    team_id: UUID,
    provider: str,
    provider_key: str | None,
    raw_name: str,
    confidence: float,
    verified: bool = False,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into team_aliases (team_id, provider, provider_key, alias, confidence, verified)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (provider, alias) do nothing
            """,
            (team_id, provider, provider_key, raw_name, confidence, verified),
        )


def _queue_review(
    conn: psycopg.Connection,
    *,
    kind: str,
    provider: str,
    provider_key: str | None,
    raw_name: str,
    sport_id: str,
    candidate_team: UUID | None,
    confidence: float | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into review_items
              (kind, provider, provider_key, raw_name, sport_id, candidate_team, confidence)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (provider, raw_name, sport_id) where status = 'pending'
            do nothing
            """,
            (kind, provider, provider_key, raw_name, sport_id, candidate_team, confidence),
        )


def resolve_team(
    conn: psycopg.Connection,
    *,
    provider: str,
    provider_key: str | None,
    raw_name: str,
    sport_id: str,
) -> ResolveOutcome:
    team_id = _alias_match(conn, provider, provider_key, raw_name)
    if team_id is not None:
        return ResolveOutcome(team_id, "alias")

    best_id: UUID | None = None
    best_score = 0.0
    for cand_id, cand_name in _candidates(conn, sport_id):
        score = fuzz.token_set_ratio(raw_name.lower(), cand_name.lower())
        if score > best_score:
            best_id, best_score = cand_id, score

    if best_id is not None and best_score >= AUTO_LINK_SCORE:
        _store_alias(
            conn,
            team_id=best_id,
            provider=provider,
            provider_key=provider_key,
            raw_name=raw_name,
            confidence=best_score / 100.0,
        )
        return ResolveOutcome(best_id, "auto_linked", best_score)

    if best_id is not None and best_score >= REVIEW_MIN_SCORE:
        _queue_review(
            conn,
            kind="ambiguous_alias",
            provider=provider,
            provider_key=provider_key,
            raw_name=raw_name,
            sport_id=sport_id,
            candidate_team=best_id,
            confidence=best_score / 100.0,
        )
        return ResolveOutcome(None, "queued_ambiguous", best_score)

    _queue_review(
        conn,
        kind="new_team",
        provider=provider,
        provider_key=provider_key,
        raw_name=raw_name,
        sport_id=sport_id,
        candidate_team=None,
        confidence=(best_score / 100.0) if best_id is not None else None,
    )
    return ResolveOutcome(None, "queued_new_team", best_score or None)


def resolve_events(conn: psycopg.Connection) -> dict[str, int]:
    """Link team ids on events that still carry raw names only.

    Returns counters for observability. Commits are the caller's job.
    """
    counters = {"events_linked": 0, "sides_resolved": 0, "sides_queued": 0}
    with conn.cursor() as cur:
        cur.execute(
            """
            select id, sport_id, provider_keys, home_name_raw, away_name_raw,
                   home_team, away_team
            from events
            where (home_team is null or away_team is null)
              and home_name_raw is not null and away_name_raw is not null
            """
        )
        rows = cur.fetchall()

    for event_id, sport_id, provider_keys, home_raw, away_raw, home_team, away_team in rows:
        provider = next(iter(provider_keys), None)
        if provider is None:
            continue
        updates: dict[str, UUID] = {}
        for col, current, raw in (
            ("home_team", home_team, home_raw),
            ("away_team", away_team, away_raw),
        ):
            if current is not None:
                continue
            outcome = resolve_team(
                conn,
                provider=provider,
                provider_key=None,
                raw_name=raw,
                sport_id=sport_id,
            )
            if outcome.team_id is not None:
                updates[col] = outcome.team_id
                counters["sides_resolved"] += 1
            else:
                counters["sides_queued"] += 1
        if updates:
            sets = ", ".join(f"{col} = %s" for col in updates)  # col names are literals above
            with conn.cursor() as cur:
                cur.execute(
                    f"update events set {sets} where id = %s",
                    (*updates.values(), event_id),
                )
        if (home_team or updates.get("home_team")) and (away_team or updates.get("away_team")):
            counters["events_linked"] += 1
    return counters


def merge_duplicate_events(conn: psycopg.Connection) -> int:
    """Merge fully-resolved events that are the same real-world match seen by
    different providers: same sport, same (unordered) team pair, commence_time
    within +/-36h. Keeps the older row, unions provider_keys, repoints
    snapshots. Events referenced by picks are never merged away (picks are
    append-only and must not be touched — CLAUDE.md §2)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select e1.id, e2.id
            from events e1
            join events e2
              on e2.sport_id = e1.sport_id
             and e1.id < e2.id
             and least(e1.home_team, e1.away_team) = least(e2.home_team, e2.away_team)
             and greatest(e1.home_team, e1.away_team) = greatest(e2.home_team, e2.away_team)
             and abs(extract(epoch from e1.commence_time - e2.commence_time)) <= %s
            where e1.home_team is not null and e1.away_team is not null
              and e2.home_team is not null and e2.away_team is not null
              and not exists (select 1 from picks p where p.event_id = e2.id)
            """,
            (EVENT_MERGE_WINDOW_HOURS * 3600,),
        )
        pairs = cur.fetchall()

    merged = 0
    with conn.cursor() as cur:
        for keep_id, dupe_id in pairs:
            cur.execute(
                """
                update events
                set provider_keys = provider_keys ||
                    (select provider_keys from events where id = %s)
                where id = %s
                """,
                (dupe_id, keep_id),
            )
            cur.execute(
                "update odds_snapshots set event_id = %s where event_id = %s",
                (keep_id, dupe_id),
            )
            # A result may have attached to the dupe before the merge (second
            # results provider). Carry it over unless the kept event already
            # has one (first-reporter-wins, §10.4), then clear the leftover so
            # the FK allows the delete. Scores were oriented to the DUPE's
            # home/away — swap them if the kept event is oriented the other way
            # (the merge matches team pairs unordered).
            cur.execute(
                "select e1.home_team = e2.away_team from events e1, events e2"
                " where e1.id = %s and e2.id = %s",
                (keep_id, dupe_id),
            )
            reversed_orientation = cur.fetchone()[0]
            cur.execute(
                """
                update event_results set event_id = %s,
                  home_score = case when %s then away_score else home_score end,
                  away_score = case when %s then home_score else away_score end
                where event_id = %s
                  and not exists (select 1 from event_results where event_id = %s)
                """,
                (keep_id, reversed_orientation, reversed_orientation, dupe_id, keep_id),
            )
            cur.execute("delete from event_results where event_id = %s", (dupe_id,))
            cur.execute("delete from ev_board where event_id = %s", (dupe_id,))
            cur.execute("delete from events where id = %s", (dupe_id,))
            merged += 1
    return merged
