import Link from "next/link";
import { fmtUtc, pct, pool } from "@/lib/db";

export const dynamic = "force-dynamic";

/**
 * Homepage = the Daily Best Bets feed (pivot ADR 0002).
 * Two label kinds, never blurred (§9 visibility-vs-badge):
 *  - "verified"    -> VERIFIED model pick (gate-passed models only)
 *  - "market_edge" -> Market-edge signal (validation ongoing)
 */
const LABEL_COPY: Record<string, string> = {
  verified: "VERIFIED model pick",
  market_edge: "Market-edge signal (validation ongoing)",
};

interface FeedRow {
  feed_date: string;
  rank: number;
  label: string;
  market: string;
  outcome: string;
  line: string | null;
  price_at_publish: string;
  bookmaker: string;
  rationale: string | null;
  commence_time: Date;
  sport: string;
  competition: string | null;
  home: string | null;
  away: string | null;
}

interface RecordStrip {
  n_picks: string;
  wins: string;
  losses: string;
  pnl: string;
  avg_clv: string | null;
  n_settled: string;
}

async function loadFeed(): Promise<FeedRow[]> {
  const { rows } = await pool.query<FeedRow>(`
    select f.feed_date::text, f.rank, f.label,
           p.market, p.outcome, p.line, p.price_at_publish, p.bookmaker,
           p.rationale, e.commence_time,
           s.name as sport, c.name as competition,
           th.canonical_name as home, ta.canonical_name as away
    from daily_feed f
    join picks p on p.id = f.pick_id
    join events e on e.id = p.event_id
    join sports s on s.id = e.sport_id
    left join competitions c on c.id = e.competition_id
    left join teams th on th.id = e.home_team
    left join teams ta on ta.id = e.away_team
    where f.feed_date = (now() at time zone 'utc')::date
    order by f.rank
  `);
  return rows;
}

async function loadRecord(): Promise<RecordStrip> {
  const { rows } = await pool.query<RecordStrip>(`
    select (select count(*) from picks) as n_picks,
           count(*) filter (where st.result = 'win') as wins,
           count(*) filter (where st.result = 'loss') as losses,
           coalesce(sum(st.pnl_units), 0) as pnl,
           avg(st.clv) as avg_clv,
           count(*) as n_settled
    from settlements st
  `);
  return rows[0];
}

export default async function DailyBestBetsPage() {
  const [feed, record] = await Promise.all([loadFeed(), loadRecord()]);
  const pnl = Number(record.pnl);
  const avgClv = record.avg_clv == null ? null : Number(record.avg_clv);

  return (
    <>
      <h1>Daily Best Bets</h1>
      <p className="sub">
        Your daily betting research desk for European sports — every pick
        explained, every result public. Frozen once per day (UTC); the record
        never changes after publication. See <Link href="/methodology">how it
        works</Link>.
      </p>

      <div className="cards">
        <div className="card">
          <div className="label">All-time picks</div>
          <div className="value">{record.n_picks}</div>
        </div>
        <div className="card">
          <div className="label">W – L (settled {record.n_settled})</div>
          <div className="value">
            {record.wins} – {record.losses}
          </div>
        </div>
        <div className="card">
          <div className="label">P&amp;L (units)</div>
          <div className={`value ${pnl >= 0 ? "pos" : "neg"}`}>
            {pnl >= 0 ? "+" : ""}
            {pnl.toFixed(2)}
          </div>
        </div>
        <div className="card">
          <div className="label">Mean CLV</div>
          <div className={`value ${avgClv == null || avgClv >= 0 ? "pos" : "neg"}`}>
            {avgClv == null ? "—" : pct(avgClv)}
          </div>
        </div>
      </div>
      <p className="muted">
        Full immutable log, losses included: <Link href="/track-record">track
        record</Link> · raw signals: <Link href="/board">+EV board</Link>
      </p>

      {feed.length === 0 ? (
        <p className="muted">
          Today&apos;s feed is not frozen yet (built each morning UTC) or no
          pick qualified today. An empty day is an honest day — check the{" "}
          <Link href="/board">+EV board</Link> for live market signals.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Kickoff</th>
              <th>Match</th>
              <th>Pick</th>
              <th className="num">Price</th>
              <th>Label</th>
            </tr>
          </thead>
          <tbody>
            {feed.map((r) => (
              <tr key={r.rank}>
                <td className="muted">{r.rank}</td>
                <td className="muted">{fmtUtc(r.commence_time)}</td>
                <td>
                  {r.home ?? "?"} – {r.away ?? "?"}
                  <div className="muted" style={{ fontSize: 12 }}>
                    {r.sport}
                    {r.competition ? ` · ${r.competition}` : ""}
                  </div>
                </td>
                <td>
                  {r.market}
                  {r.line != null ? ` ${r.line}` : ""} — {r.outcome} @ {r.bookmaker}
                  {r.rationale ? <div className="rationale">{r.rationale}</div> : null}
                </td>
                <td className="num">{Number(r.price_at_publish).toFixed(2)}</td>
                <td>
                  <span className={`badge ${r.label === "verified" ? "ev" : "shadow"}`}>
                    {LABEL_COPY[r.label] ?? LABEL_COPY.market_edge}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
