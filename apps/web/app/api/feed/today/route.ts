import { NextResponse } from "next/server";
import { pool } from "@/lib/db";

export const dynamic = "force-dynamic";

/**
 * GET /api/feed/today — today's frozen Daily Best Bets feed (UTC), JSON.
 * Labels mirror §9 visibility-vs-badge: "verified" only for gate-passed
 * models; everything else is a clearly-labeled market-edge signal.
 */
export async function GET() {
  const { rows } = await pool.query(`
    select f.feed_date::text, f.rank, f.label, f.pick_id,
           p.market, p.outcome, p.line, p.price_at_publish, p.bookmaker,
           p.published_at, p.rationale, m.id as model_id,
           e.commence_time, s.name as sport, c.name as competition,
           th.canonical_name as home, ta.canonical_name as away
    from daily_feed f
    join picks p on p.id = f.pick_id
    join models m on m.id = p.model_id
    join events e on e.id = p.event_id
    join sports s on s.id = e.sport_id
    left join competitions c on c.id = e.competition_id
    left join teams th on th.id = e.home_team
    left join teams ta on ta.id = e.away_team
    where f.feed_date = (now() at time zone 'utc')::date
    order by f.rank
  `);

  return NextResponse.json({
    feed_date: rows.length > 0 ? rows[0].feed_date : null,
    frozen: rows.length > 0,
    entries: rows.map((r) => ({
      rank: r.rank,
      label: r.label,
      pick_id: r.pick_id,
      model_id: r.model_id,
      sport: r.sport,
      competition: r.competition,
      match: `${r.home ?? "?"} – ${r.away ?? "?"}`,
      commence_time: r.commence_time,
      market: r.market,
      line: r.line == null ? null : Number(r.line),
      outcome: r.outcome,
      price_at_publish: Number(r.price_at_publish),
      bookmaker: r.bookmaker,
      published_at: r.published_at,
      rationale: r.rationale,
    })),
    notice:
      "18+ | Educational research, not betting advice — outcomes are always uncertain. Bet responsibly: begambleaware.org | gamblingtherapy.org | jocresponsabil.ro",
  });
}
