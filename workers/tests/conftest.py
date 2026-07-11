import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

TEST_DSN = os.environ.get("TEST_DATABASE_URL")

requires_db = pytest.mark.skipif(
    not TEST_DSN, reason="TEST_DATABASE_URL not set (integration tests run in CI)"
)


@pytest.fixture
def conn():
    connection = psycopg.connect(TEST_DSN)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture
def event_id(conn):
    """A skeleton event 1h in the past (so closing capture applies)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into events (sport_id, commence_time, provider_keys)
            values ('cs2', %s, %s::jsonb)
            returning id
            """,
            (
                datetime.now(UTC) - timedelta(hours=1),
                f'{{"test": "{uuid.uuid4()}"}}',
            ),
        )
        return cur.fetchone()[0]


@pytest.fixture
def model_id(conn):
    mid = f"test_model_{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into models (id, sport_id, market, version, status)
            values (%s, 'cs2', 'h2h', 'v0', 'research')
            """,
            (mid,),
        )
    return mid
