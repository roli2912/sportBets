import { fmtUtc, pct, pool } from "@/lib/db";

export const dynamic = "force-dynamic";

interface PickRow {
  published_at: Date;
  market: string;
  outcome: string;
  price_at_publish: string;
  bookmaker: string;
  stake_units: string;
  rationale: string | null;
  result: string | null;
  closing_price: string | null;
  clv: string | null;
  pnl_units: string | null;
  model_id: string;
  model_status: string;
  home: string | null;
  away: string | null;
  competition: string | null;
  sport: string;
}

async function loadPicks(): Promise<PickRow[]> {
  const { rows } = await pool.query<PickRow>(`
    select p.published_at, p.market, p.outcome, p.price_at_publish, p.bookmaker,
           p.stake_units, p.rationale,
           st.result, st.closing_price, st.clv, st.pnl_units,
           m.id as model_id, m.status as model_status,
           th.canonical_name as home, ta.canonical_name as away,
           c.name as competition, s.name as sport
    from picks p
    join models m on m.id = p.model_id
    join events e on e.id = p.event_id
    join sports s on s.id = e.sport_id
    left join settlements st on st.pick_id = p.id
    left join competitions c on c.id = e.competition_id
    left join teams th on th.id = e.home_team
    left join teams ta on ta.id = e.away_team
    order by p.published_at desc
  `);
  return rows;
}

export default async function TrackRecordPage() {
  const rows = await loadPicks();
  const settled = rows.filter((r) => r.result);
  const wins = settled.filter((r) => r.result === "win").length;
  const losses = settled.filter((r) => r.result === "loss").length;
  const pnl = settled.reduce((a, r) => a + Number(r.pnl_units ?? 0), 0);
  const staked = settled.reduce((a, r) => a + Number(r.stake_units), 0);
  const avgClv =
    settled.length > 0
      ? settled.reduce((a, r) => a + Number(r.clv ?? 0), 0) / settled.length
      : 0;

  return (
    <>
      <h1>Track record</h1>
      <p className="sub">
        Every pick is immutable at publication and graded against the no-vig
        closing line (CLV). All results shown — wins and losses. CLV is the
        primary KPI; win rate alone is a vanity metric at small N.
      </p>

      <div className="cards">
        <div className="card">
          <div className="label">Picks (N)</div>
          <div className="value">{rows.length}</div>
        </div>
        <div className="card">
          <div className="label">W – L</div>
          <div className="value">
            {wins} – {losses}
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
          <div className="label">Flat ROI</div>
          <div className={`value ${pnl >= 0 ? "pos" : "neg"}`}>
            {staked > 0 ? pct(pnl / staked) : "—"}
          </div>
        </div>
        <div className="card">
          <div className="label">Mean CLV</div>
          <div className={`value ${avgClv >= 0 ? "pos" : "neg"}`}>{pct(avgClv)}</div>
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="muted">No picks yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Published</th>
              <th>Match</th>
              <th>Pick</th>
              <th className="num">Price</th>
              <th className="num">Close (fair)</th>
              <th className="num">CLV</th>
              <th className="num">Stake</th>
              <th>Result</th>
              <th className="num">P&amp;L</th>
              <th>Model</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const clv = r.clv == null ? null : Number(r.clv);
              const rpnl = r.pnl_units == null ? null : Number(r.pnl_units);
              return (
                <tr key={i}>
                  <td className="muted">{fmtUtc(r.published_at)}</td>
                  <td>
                    {r.home ?? "?"} – {r.away ?? "?"}
                    <div className="muted" style={{ fontSize: 12 }}>
                      {r.sport}
                      {r.competition ? ` · ${r.competition}` : ""}
                    </div>
                  </td>
                  <td>
                    {r.market} / {r.outcome} @ {r.bookmaker}
                    {r.rationale ? <div className="rationale">{r.rationale}</div> : null}
                  </td>
                  <td className="num">{Number(r.price_at_publish).toFixed(2)}</td>
                  <td className="num">
                    {r.closing_price ? Number(r.closing_price).toFixed(2) : "—"}
                  </td>
                  <td className={`num ${clv == null ? "" : clv >= 0 ? "pos" : "neg"}`}>
                    {clv == null ? "—" : pct(clv)}
                  </td>
                  <td className="num">{Number(r.stake_units).toFixed(2)}u</td>
                  <td>
                    {r.result ? (
                      <span className={`badge ${r.result}`}>{r.result}</span>
                    ) : (
                      <span className="badge">open</span>
                    )}
                  </td>
                  <td className={`num ${rpnl == null ? "" : rpnl >= 0 ? "pos" : "neg"}`}>
                    {rpnl == null ? "—" : `${rpnl >= 0 ? "+" : ""}${rpnl.toFixed(2)}u`}
                  </td>
                  <td>
                    <span className={`badge ${r.model_status === "shadow" ? "shadow" : ""}`}>
                      {r.model_id} · {r.model_status}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}
