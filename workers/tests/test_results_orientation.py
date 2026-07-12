"""Integration tests: esports result scores align to the event's orientation.

Esports has no home/away — each provider orders teams arbitrarily, and after a
cross-provider merge the event's orientation can differ from the result's.
persist_results must align name-carrying results via team_aliases and skip
(never guess) anything unresolved (CLAUDE.md §7); merge_duplicate_events must
carry event_results over with the same orientation care.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from core.db import persist_results
from core.types import Result
from resolver.resolver import merge_duplicate_events
from tests.conftest import requires_db

pytestmark = requires_db

PROVIDER = "pandascore"


def _mk_team(conn, name: str):
    with conn.cursor() as cur:
        cur.execute(
            "insert into teams (sport_id, canonical_name) values ('cs2', %s) returning id",
            (name,),
        )
        return cur.fetchone()[0]


def _mk_alias(conn, team_id, alias: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into team_aliases (team_id, provider, alias, confidence, verified)
            values (%s, %s, %s, 1.0, true)
            """,
            (team_id, PROVIDER, alias),
        )


def _mk_event(conn, *, provider_keys: dict[str, str], home=None, away=None, hours: float = -2.0):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into events (sport_id, commence_time, provider_keys, home_team, away_team)
            values ('cs2', %s, %s::jsonb, %s, %s)
            returning id
            """,
            (datetime.now(UTC) + timedelta(hours=hours), json.dumps(provider_keys), home, away),
        )
        return cur.fetchone()[0]


def _result(key: str, *, home_name=None, away_name=None, home_score=2, away_score=0) -> Result:
    return Result(
        provider=PROVIDER,
        provider_key=key,
        sport_id="cs2",
        home_score=home_score,
        away_score=away_score,
        home_name=home_name,
        away_name=away_name,
        finished_at=datetime.now(UTC),
    )


def _scores(conn, event_id):
    with conn.cursor() as cur:
        cur.execute(
            "select home_score, away_score from event_results where event_id = %s",
            (event_id,),
        )
        return cur.fetchone()


def test_same_orientation_written_as_is(conn):
    t1, t2 = _mk_team(conn, "NAVI"), _mk_team(conn, "Spirit")
    _mk_alias(conn, t1, "Natus Vincere")
    _mk_alias(conn, t2, "Team Spirit")
    key = f"k-{uuid.uuid4()}"
    eid = _mk_event(conn, provider_keys={PROVIDER: key}, home=t1, away=t2)

    n = persist_results(conn, [_result(key, home_name="Natus Vincere", away_name="Team Spirit")])
    assert n == 1
    assert _scores(conn, eid) == (2, 0)


def test_reversed_orientation_swaps_scores(conn):
    """The provider reported the match as (Spirit 2 - 0 NAVI) but our event is
    (NAVI vs Spirit) — blind positional writes would grade the pick wrong."""
    t1, t2 = _mk_team(conn, "NAVI"), _mk_team(conn, "Spirit")
    _mk_alias(conn, t1, "Natus Vincere")
    _mk_alias(conn, t2, "Team Spirit")
    key = f"k-{uuid.uuid4()}"
    eid = _mk_event(conn, provider_keys={PROVIDER: key}, home=t1, away=t2)

    n = persist_results(conn, [_result(key, home_name="Team Spirit", away_name="Natus Vincere")])
    assert n == 1
    assert _scores(conn, eid) == (0, 2)


def test_unresolved_alias_skips_never_guesses(conn):
    t1, t2 = _mk_team(conn, "NAVI"), _mk_team(conn, "Spirit")
    _mk_alias(conn, t1, "Natus Vincere")  # away name has NO alias yet
    key = f"k-{uuid.uuid4()}"
    eid = _mk_event(conn, provider_keys={PROVIDER: key}, home=t1, away=t2)

    n = persist_results(conn, [_result(key, home_name="Natus Vincere", away_name="Unknown Squad")])
    assert n == 0
    assert _scores(conn, eid) is None  # blocked visibly; retried next poll


def test_unresolved_event_teams_skip(conn):
    key = f"k-{uuid.uuid4()}"
    eid = _mk_event(conn, provider_keys={PROVIDER: key})  # home/away still NULL

    n = persist_results(conn, [_result(key, home_name="Natus Vincere", away_name="Team Spirit")])
    assert n == 0
    assert _scores(conn, eid) is None


def test_nameless_result_stays_positional(conn):
    """Football-style results (real home/away) carry no names — unchanged path."""
    key = f"k-{uuid.uuid4()}"
    eid = _mk_event(conn, provider_keys={PROVIDER: key})
    n = persist_results(conn, [_result(key, home_score=3, away_score=1)])
    assert n == 1
    assert _scores(conn, eid) == (3, 1)


def test_merge_carries_result_with_orientation_swap(conn):
    """A result attached to the dupe before the merge must survive on the kept
    event, swapped if the two events are oriented opposite ways."""
    t1, t2 = _mk_team(conn, "NAVI"), _mk_team(conn, "Spirit")
    a = _mk_event(conn, provider_keys={"oddspapi": f"a-{uuid.uuid4()}"}, home=t1, away=t2, hours=24)
    b = _mk_event(conn, provider_keys={PROVIDER: f"b-{uuid.uuid4()}"}, home=t2, away=t1, hours=30)
    # Result on b, in b's orientation: Spirit (b's home) won 2-0.
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into event_results (event_id, provider, home_score, away_score, finished_at)
            values (%s, %s, 2, 0, now())
            """,
            (b, PROVIDER),
        )

    assert merge_duplicate_events(conn) == 1

    with conn.cursor() as cur:
        cur.execute("select id, home_team from events where id = any(%s)", ([a, b],))
        rows = cur.fetchall()
        assert len(rows) == 1
        survivor, survivor_home = rows[0]
        cur.execute(
            "select count(*), min(home_score), min(away_score)"
            " from event_results where event_id = %s",
            (survivor,),
        )
        count, home_score, away_score = cur.fetchone()
    assert count == 1
    # Spirit won 2-0 regardless of which row survived the merge.
    if survivor_home == t2:
        assert (home_score, away_score) == (2, 0)
    else:
        assert (home_score, away_score) == (0, 2)
