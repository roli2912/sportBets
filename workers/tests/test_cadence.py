from datetime import UTC, datetime, timedelta

from collectors.base import due_for_poll, poll_interval


def td(**kw):
    return timedelta(**kw)


def test_cadence_bands_match_claude_md_section_8():
    assert poll_interval(td(hours=72)) == td(days=1)  # > 48h: 1/day
    assert poll_interval(td(hours=36)) == td(hours=6)  # 48-24h: 4/day
    assert poll_interval(td(hours=12)) == td(hours=1)  # 24-2h: hourly
    assert poll_interval(td(hours=1)) == td(minutes=5)  # 2h-15m: every 5 min
    assert poll_interval(td(minutes=10)) == td(minutes=2)  # final 15m: every 2 min


def test_cadence_boundaries():
    assert poll_interval(td(hours=48, seconds=1)) == td(days=1)
    assert poll_interval(td(hours=48)) == td(hours=6)
    assert poll_interval(td(hours=24)) == td(hours=1)
    assert poll_interval(td(hours=2)) == td(minutes=5)
    assert poll_interval(td(minutes=15)) == td(minutes=2)


def test_no_polling_after_kickoff():
    assert poll_interval(timedelta(0)) is None
    assert poll_interval(td(minutes=-5)) is None


def test_due_for_poll():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    kickoff = now + td(hours=1)  # 5-min band
    assert due_for_poll(kickoff, None, now)
    assert not due_for_poll(kickoff, now - td(minutes=3), now)
    assert due_for_poll(kickoff, now - td(minutes=5), now)
    # already kicked off -> collector stands down
    assert not due_for_poll(now - td(minutes=1), None, now)
