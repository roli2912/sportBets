import { fmtUtc, pct, pool } from "@/lib/db";

export const dynamic = "force-dynamic";

const EV_THRESHOLD = 0.025; // display mirror of ENGINE_EV_THRESHOLD (§8)

interface BoardRow {
  bookmaker: string;
  market: string;
  outcome: string;
  line: string | null;
  price: string;
  sharp_price: string;
  novig_price: string;
  p_true: string;
  ev: string;
  captured_at: Date;
  commence_time: Date;
  sport: string;
  competition: string | null;
  home: string | null;
  away: string | null;
}

async function loadBoard(): Promise<BoardRow[]> {
  const { rows } = await pool.query<BoardRow>(`
    select b.bookmaker, b.market, b.outcome, b.line, b.price, b.sharp_price,
           b.novig_price, b.p_true, b.ev, b.captured_at,
           e.commence_time, s.name as sport, c.name as competition,
           th.canonical_name as home, ta.canonical_name as away
    from ev_board b
    join events e on e.id = b.event_id
    join sports s on s.id = e.sport_id
    left join competitions c on c.id = e.competition_id
    left join teams th on th.id = e.home_team
    left join teams ta on ta.id = e.away_team
    order by b.ev desc
  `);
  return rows;
}

export default async function EvBoardPage() {
  const rows = await loadBoard();
  const positive = rows.filter((r) => Number(r.ev) >= EV_THRESHOLD);

  return (
    <>
      <h1>+EV board</h1>
      <p className="sub">
        Soft-bookmaker prices vs the de-vigged sharp reference. Educational
        market analysis — rows at or above {pct(EV_THRESHOLD)} EV are flagged.
      </p>

      <div className="cards">
        <div className="card">
          <div className="label">Candidates</div>
          <div className="value">{rows.length}</div>
        </div>
        <div className="card">
          <div className="label">≥ {pct(EV_THRESHOLD)} EV</div>
          <div className="value">{positive.length}</div>
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="muted">
          Board is empty — run the seed + refresh jobs (see workers/tools).
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Kickoff</th>
              <th>Match</th>
              <th>Market</th>
              <th>Outcome</th>
              <th>Book</th>
              <th className="num">Price</th>
              <th className="num">Fair (no-vig)</th>
              <th className="num">p(true)</th>
              <th className="num">EV</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const ev = Number(r.ev);
              return (
                <tr key={i}>
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
                    {r.line != null ? ` ${r.line}` : ""}
                  </td>
                  <td>{r.outcome}</td>
                  <td>{r.bookmaker}</td>
                  <td className="num">{Number(r.price).toFixed(2)}</td>
                  <td className="num">{Number(r.novig_price).toFixed(2)}</td>
                  <td className="num">{(Number(r.p_true) * 100).toFixed(1)}%</td>
                  <td className={`num ${ev >= EV_THRESHOLD ? "pos" : ev < 0 ? "neg" : ""}`}>
                    {pct(ev)}
                  </td>
                  <td>{ev >= EV_THRESHOLD ? <span className="badge ev">+EV</span> : null}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}
