"""Integration tests for the entity resolver (CLAUDE.md §7).

Pre-registered bands: >=92 auto-link, 80-92 review, <80 new-team. Tests use
names whose rapidfuzz scores land in each band and assert the band as computed
by the same scorer, so a rapidfuzz upgrade that shifts scores fails loudly.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from rapidfuzz import fuzz

from resolver.resolver import (
    AUTO_LINK_SCORE,
    REVIEW_MIN_SCORE,
    merge_duplicate_events,
    resolve_events,
    resolve_team,
)
from tests.conftest import requires_db

pytestmark = requires_db


def _mk_team(conn, name: str, sport: str = "cs2"):
    with conn.cursor() as cur:
        cur.execute(
            "insert into teams (sport_id, canonical_name) values (%s, %s) returning id",
            (sport, name),
        )
        return cur.fetchone()[0]


def _mk_event(
    conn,
    *,
    provider_keys: dict[str, str],
    home=None,
    away=None,
    home_raw=None,
    away_raw=None,
    hours_ahead: float = 24.0,
    sport: str = "cs2",
):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into events
              (sport_id, commence_time, provider_keys, home_team, away_team,
               home_name_raw, away_name_raw)
            values (%s, %s, %s::jsonb, %s, %s, %s, %s)
            returning id
            """,
            (
                sport,
                datetime.now(UTC) + timedelta(hours=hours_ahead),
                json.dumps(provider_keys),
                home,
                away,
                home_raw,
                away_raw,
            ),
        )
        return cur.fetchone()[0]


def _review_items(conn, raw_name: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "select kind, candidate_team, status from review_items where raw_name = %s",
            (raw_name,),
        )
        return cur.fetchall()


# ---------------------------------------------------------------- resolve_team


def test_exact_alias_match_wins(conn):
    tid = _mk_team(conn, "Natus Vincere")
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into team_aliases (team_id, provider, provider_key, alias, confidence, verified)
            values (%s, 'oddspapi', 'navi-key', 'NAVI', 1.0, true)
            """,
            (tid,),
        )
    out = resolve_team(
        conn, provider="oddspapi", provider_key=None, raw_name="navi", sport_id="cs2"
    )
    assert out.team_id == tid
    assert out.action == "alias"


def test_high_score_auto_links_and_stores_unverified_alias(conn):
    tid = _mk_team(conn, "Natus Vincere")
    raw = "natus vincere esports"  # token-set subset -> 100
    assert fuzz.token_set_ratio(raw, "natus vincere") >= AUTO_LINK_SCORE

    out = resolve_team(conn, provider="oddspapi", provider_key="pk1", raw_name=raw, sport_id="cs2")
    assert out.team_id == tid
    assert out.action == "auto_linked"

    with conn.cursor() as cur:
        cur.execute(
            "select team_id, verified from team_aliases where provider = 'oddspapi' and alias = %s",
            (raw,),
        )
        alias_team, verified = cur.fetchone()
    assert alias_team == tid
    assert verified is False


def test_mid_score_queues_ambiguous_and_does_not_link(conn):
    tid = _mk_team(conn, "FaZe Clan")
    raw = "FaZe Klan"
    score = fuzz.token_set_ratio(raw.lower(), "faze clan")
    assert REVIEW_MIN_SCORE <= score < AUTO_LINK_SCORE, score

    out = resolve_team(conn, provider="oddspapi", provider_key=None, raw_name=raw, sport_id="cs2")
    assert out.team_id is None
    assert out.action == "queued_ambiguous"
    items = _review_items(conn, raw)
    assert items == [("ambiguous_alias", tid, "pending")]


def test_low_score_queues_new_team(conn):
    _mk_team(conn, "Natus Vincere")
    out = resolve_team(
        conn, provider="oddspapi", provider_key=None, raw_name="Cloud9", sport_id="cs2"
    )
    assert out.team_id is None
    assert out.action == "queued_new_team"
    assert _review_items(conn, "Cloud9") == [("new_team", None, "pending")]


def test_repeat_sightings_do_not_spam_queue(conn):
    for _ in range(3):
        resolve_team(
            conn, provider="oddspapi", provider_key=None, raw_name="Cloud9", sport_id="cs2"
        )
    assert len(_review_items(conn, "Cloud9")) == 1


# -------------------------------------------------------------- resolve_events


def test_resolve_events_links_sides_via_aliases(conn):
    home = _mk_team(conn, "Natus Vincere")
    away = _mk_team(conn, "Team Spirit")
    eid = _mk_event(
        conn,
        provider_keys={"oddspapi": f"k-{uuid.uuid4()}"},
        home_raw="Natus Vincere",
        away_raw="Team Spirit",
    )
    counters = resolve_events(conn)
    assert counters["events_linked"] >= 1
    with conn.cursor() as cur:
        cur.execute("select home_team, away_team from events where id = %s", (eid,))
        assert cur.fetchone() == (home, away)


def test_resolve_events_leaves_unresolved_sides_null(conn):
    _mk_team(conn, "Natus Vincere")
    eid = _mk_event(
        conn,
        provider_keys={"oddspapi": f"k-{uuid.uuid4()}"},
        home_raw="Natus Vincere",
        away_raw="Totally Unknown Squad",
    )
    resolve_events(conn)
    with conn.cursor() as cur:
        cur.execute("select home_team, away_team from events where id = %s", (eid,))
        home_team, away_team = cur.fetchone()
    assert home_team is not None
    assert away_team is None
    assert _review_items(conn, "Totally Unknown Squad") != []


# ------------------------------------------------------- merge_duplicate_events


def test_merge_unions_keys_and_repoints_snapshots(conn):
    t1 = _mk_team(conn, "Natus Vincere")
    t2 = _mk_team(conn, "Team Spirit")
    a = _mk_event(conn, provider_keys={"oddspapi": "a"}, home=t1, away=t2, hours_ahead=24)
    # reversed home/away + 12h offset: still the same real-world match
    b = _mk_event(conn, provider_keys={"therundown": "b"}, home=t2, away=t1, hours_ahead=36)
    with conn.cursor() as cur:
        cur.execute(
            "select ensure_odds_snapshots_partition(%s)",
            (datetime.now(UTC).date().replace(day=1),),
        )
        cur.execute(
            """
            insert into odds_snapshots
              (event_id, bookmaker, market, outcome, price, captured_at)
            values (%s, 'pinnacle', 'h2h', 'home', 1.9, now())
            """,
            (b,),
        )

    assert merge_duplicate_events(conn) == 1

    # which uuid survives is arbitrary (keeps e1.id < e2.id); assert invariants
    with conn.cursor() as cur:
        cur.execute("select id, provider_keys from events where id = any(%s)", ([a, b],))
        rows = cur.fetchall()
        assert len(rows) == 1
        survivor, keys = rows[0]
        assert keys == {"oddspapi": "a", "therundown": "b"}
        cur.execute("select count(*) from odds_snapshots where event_id = %s", (survivor,))
        assert cur.fetchone()[0] == 1


def test_merge_skips_events_outside_window(conn):
    t1 = _mk_team(conn, "Natus Vincere")
    t2 = _mk_team(conn, "Team Spirit")
    # 48h apart -> outside the +/-36h window
    _mk_event(conn, provider_keys={"a": "1"}, home=t1, away=t2, hours_ahead=0)
    _mk_event(conn, provider_keys={"b": "2"}, home=t1, away=t2, hours_ahead=48)
    assert merge_duplicate_events(conn) == 0


def test_merge_never_deletes_event_with_picks(conn, model_id):
    t1 = _mk_team(conn, "Natus Vincere")
    t2 = _mk_team(conn, "Team Spirit")
    picked = _mk_event(conn, provider_keys={"a": "1"}, home=t1, away=t2, hours_ahead=1)
    other = _mk_event(conn, provider_keys={"b": "2"}, home=t1, away=t2, hours_ahead=2)
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into picks
              (model_id, event_id, market, outcome, price_at_publish, bookmaker, stake_units)
            values (%s, %s, 'h2h', 'home', 2.1, 'pinnacle', 0.5)
            """,
            (model_id, picked),
        )
        cur.execute(
            """
            insert into picks
              (model_id, event_id, market, outcome, price_at_publish, bookmaker, stake_units)
            values (%s, %s, 'h2h', 'away', 1.9, 'pinnacle', 0.5)
            """,
            (model_id, other),
        )
    # both sides carry picks -> neither row may ever be merged away
    assert merge_duplicate_events(conn) == 0
    with conn.cursor() as cur:
        cur.execute("select count(*) from events where id = any(%s)", ([picked, other],))
        assert cur.fetchone()[0] == 2
