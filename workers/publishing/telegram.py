"""Telegram publisher (§13 Task 4): per-pick posts + morning digest.

Every message ends with the §2.5 compliance line (publishing.templates owns
the copy; this module never writes its own). Idempotent via outbound_posts:
a pick is posted once, a feed date's digest is posted once — re-runs no-op.

Only pre-match picks are posted (a pick surfacing after kickoff has no
actionable value and looks like retro-fitting). The immutable pick row is
the source of truth; Telegram is just a mirror of it.

Env: TELEGRAM_API_KEY (bot token), TELEGRAM_CHAT_ID (channel/group id),
TELEGRAM_BACKFILL_HOURS (default 24 — never dump deep history on first run).

Run once: uv run python -m publishing.telegram
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import httpx
import psycopg

from core.db import connect
from publishing.templates import STATUS_LABELS, format_daily_digest, format_pick

CHANNEL = "telegram"
API_BASE = "https://api.telegram.org"

# daily_feed.label -> the same honest copy the site uses (§9 label split).
FEED_LABELS = {
    "verified": STATUS_LABELS["public"],
    "market_edge": STATUS_LABELS["shadow"],
}

Sender = Callable[[str], None]


class TelegramSender:
    """Thin Bot API client; raises on any non-2xx so callers see failures."""

    def __init__(self, token: str, chat_id: str, timeout: float = 20.0) -> None:
        self._url = f"{API_BASE}/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout

    def __call__(self, text: str) -> None:
        resp = httpx.post(
            self._url,
            json={"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True},
            timeout=self._timeout,
        )
        resp.raise_for_status()


def sender_from_env() -> TelegramSender | None:
    token = os.environ.get("TELEGRAM_API_KEY", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    return TelegramSender(token, chat_id)


def post_new_picks(
    conn: psycopg.Connection,
    send: Sender,
    *,
    now: datetime | None = None,
    backfill_hours: float | None = None,
) -> int:
    """Post every unposted, still-pre-match pick published within the
    backfill window. The delivery record is inserted right after each send;
    the CALLER commits (engine convention) — worst case on a crash is one
    tick's worth of duplicate posts, never silent gaps."""
    now = now or datetime.now(UTC)
    if backfill_hours is None:
        backfill_hours = float(os.environ.get("TELEGRAM_BACKFILL_HOURS", "24"))
    with conn.cursor() as cur:
        cur.execute(
            """
            select p.id, p.market, p.outcome, p.line, p.price_at_publish,
                   p.bookmaker, p.published_at, p.rationale, m.status,
                   coalesce(th.canonical_name, '?') || ' – ' ||
                   coalesce(ta.canonical_name, '?') as match
            from picks p
            join models m on m.id = p.model_id
            join events e on e.id = p.event_id
            left join teams th on th.id = e.home_team
            left join teams ta on ta.id = e.away_team
            where e.commence_time > %(now)s
              and p.published_at >= %(since)s
              and not exists (
                select 1 from outbound_posts o
                where o.channel = %(channel)s and o.kind = 'pick' and o.pick_id = p.id
              )
            order by p.published_at, p.id
            """,
            {
                "now": now,
                "since": now - timedelta(hours=backfill_hours),
                "channel": CHANNEL,
            },
        )
        rows = cur.fetchall()

    posted = 0
    for (
        pick_id,
        market,
        outcome,
        line,
        price,
        bookmaker,
        published_at,
        rationale,
        status,
        match,
    ) in rows:
        text = format_pick(
            match=match,
            market=market,
            outcome=outcome,
            price=float(price),
            bookmaker=bookmaker,
            published_at=published_at,
            model_status=status,
            line=float(line) if line is not None else None,
            rationale=rationale,
        )
        send(text)
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into outbound_posts (channel, kind, pick_id)
                values (%s, 'pick', %s)
                on conflict do nothing
                """,
                (CHANNEL, pick_id),
            )
        posted += 1
    return posted


def post_daily_digest(
    conn: psycopg.Connection,
    send: Sender,
    feed_date: date | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """Morning digest of the frozen Daily Best Bets feed. No frozen feed or
    already posted -> False, nothing sent."""
    now = now or datetime.now(UTC)
    feed_date = feed_date or now.date()
    with conn.cursor() as cur:
        cur.execute(
            """
            select 1 from outbound_posts
            where channel = %s and kind = 'digest' and feed_date = %s
            """,
            (CHANNEL, feed_date),
        )
        if cur.fetchone() is not None:
            return False
        cur.execute(
            """
            select f.rank, f.label, p.market, p.outcome, p.line,
                   p.price_at_publish, p.bookmaker,
                   coalesce(th.canonical_name, '?') || ' – ' ||
                   coalesce(ta.canonical_name, '?') as match
            from daily_feed f
            join picks p on p.id = f.pick_id
            join events e on e.id = p.event_id
            left join teams th on th.id = e.home_team
            left join teams ta on ta.id = e.away_team
            where f.feed_date = %s
            order by f.rank
            """,
            (feed_date,),
        )
        rows = cur.fetchall()
    if not rows:
        return False  # nothing frozen yet — the digest waits, never invents

    entries = []
    for rank, label, market, outcome, line, price, bookmaker, match in rows:
        market_txt = f"{market} {float(line):g}" if line is not None else market
        entries.append(
            f"{rank}. [{FEED_LABELS.get(label, FEED_LABELS['market_edge'])}]\n"
            f"{match}\n"
            f"{market_txt} — {outcome} @ {float(price):.2f} ({bookmaker})"
        )
    send(format_daily_digest(str(feed_date), entries))
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into outbound_posts (channel, kind, feed_date)
            values (%s, 'digest', %s)
            on conflict do nothing
            """,
            (CHANNEL, feed_date),
        )
    return True


def main() -> None:
    send = sender_from_env()
    if send is None:
        print("telegram: TELEGRAM_API_KEY / TELEGRAM_CHAT_ID not set — nothing sent")
        return
    with connect() as conn:
        digest = post_daily_digest(conn, send)
        n = post_new_picks(conn, send)
        conn.commit()
    print(f"telegram: digest {'sent' if digest else 'skipped'}, {n} pick posts")


if __name__ == "__main__":
    main()
