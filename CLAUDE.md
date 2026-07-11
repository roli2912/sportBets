# CLAUDE.md — Multi-Sport Betting Analytics Platform (working name: TBD)

This file is the project foundation for Claude Code. Read it fully before making changes.
It encodes product strategy, architecture, data contracts, math, validation gates, and
non-negotiable rules. When code and this file conflict, flag it — don't silently pick one.

Last reviewed: 2026-07-10. Provider pricing changes often — verify before subscribing.

---

## 1. What this project is

A Europe-first, multi-sport betting analytics platform built by a solo developer.
Two product layers that scale differently:

- **Layer 1 — Market engine (horizontal, all sports from day one):** +EV detection,
  line shopping, and closing-line tracking. Compares soft-bookmaker prices against a
  de-vigged sharp reference. Requires no sport expertise — only odds data and math.
  This layer IS the multi-sport coverage.
- **Layer 2 — Flagship predictive models (vertical, added one sport at a time):**
  Proprietary models with public, CLV-graded track records. Launch order:
  CS2 (founder domain expertise), then one soft football vertical (Liga I Romania /
  Eredivisie), then expand (Dota, Valorant, more leagues) — each only after passing
  the validation gates in §9.

Differentiators, in priority order:
1. **Radical verification** — every pick timestamped pre-match, graded vs closing line,
   losses public, records immutable. Trust is the product.
2. **Europe-first** — European football depth + esports, decimal odds, EU bookmakers.
   Incumbents (Rithmm, PropsBot, Dimers) are US-centric.
3. **Agentic research copilot** — explanations of *why*, news-lag detection, not a
   black-box pick generator.

Business model order: subscriptions → B2B data/API → affiliates ONLY after legal
sign-off (see §12). Users betting the picks may or may not profit; the platform never
promises they will.

---

## 2. Non-negotiable product principles

These override feature requests. Do not weaken them in any commit.

1. Every pick is **immutable at publication**: timestamp, market, outcome, exact odds,
   bookmaker, stake suggestion, model version. INSERT-only, enforced at the DB layer.
2. Every pick is **graded against the captured closing line (CLV)** and settled result.
   All results public — wins, losses, pushes, voids. No deletions, no retro-edits.
3. Models reach the public feed **only via the pre-registered gates in §9**. The gates
   were written before seeing results; changing them after the fact is forbidden.
4. **No profit guarantees** anywhere — code, copy, marketing, bot messages. Banned
   strings (CI-linted): "guaranteed", "sure win", "can't lose", "risk-free profit".
   Framing is always educational/analytical.
5. **18+ and responsible-gambling links** on every public surface (site footer,
   Telegram/Discord bot messages, emails).
6. The product **never places bets and never handles wagers** — analytics only. This
   keeps it outside B2C gambling licensing. Do not add bet-placement integrations.

---

## 3. Architecture overview

```
 Odds APIs          Football stats        Esports stats
 (TheRundown,       (API-Football,        (PandaScore free tier,
  OddsPapi,          football-data.org)    GRID program)
  The Odds API)          |                     |
      |                  +----------+----------+
      v                             v
 [Odds collectors]           [Sport adapters]        <- plug-in per sport
  polling schedule,           fixtures/results/stats
  closing-line capture               |
      |                              v
      +----------------->  [Entity resolver]         <- canonical IDs, alias tables,
                             fuzzy match + review queue   manual review for low confidence
                                     |
                                     v
                        [Postgres (Supabase)]
                         entities, events, picks,
                         odds_snapshots (partitioned)
                                     |
                    +----------------+----------------+
                    v                                 v
             [+EV engine]                     [Model registry]
              de-vig sharp ref,                per-sport models,
              flag mispriced lines             backtest harness, gates
                    |                                 |
                    +---------------+-----------------+
                                    v
                              [Pick engine]
                               edge thresholds, Kelly sizing,
                               immutable publish
                                    |
                    +---------------+-----------------+
                    v                                 v
              [Delivery]                        [Grader]
               Next.js site,                     settles results,
               Telegram, Discord                 computes CLV
                                                      |
                                          feeds public track record (loop)

 Side channel: [Agent layer] — news watchers (stale-line candidates -> admin review),
               pick explainer (structured features -> rationale text).
```

Design rule: everything above the decision layer is sport-agnostic plumbing.
**Adding a sport = one new SportAdapter + one new FeatureModule + model entries.
Nothing upstream or downstream changes.** Keep it a modular monolith — one repo,
no microservices. One person maintains this.

---

## 4. Repo layout

```
/apps/web            Next.js (App Router) — public site, track record, admin
/workers             Python 3.12 — collectors, resolver, features, models, grader, agents
  /collectors        odds + stats ingestion (one module per provider)
  /adapters          SportAdapter implementations (cs2.py, football.py, ...)
  /features          FeatureModule implementations per sport
  /models            training, registry, prediction
  /engine            devig, ev, kelly, pick publishing
  /grading           settlement + CLV
  /agents            news_watcher, explainer (Claude API)
/packages/shared     market taxonomy, JSON schemas shared between web and workers
/db/migrations       Supabase CLI migrations (never edit an applied migration)
/docs/providers      one .md per provider: auth, endpoints, quirks, raw sample payloads
/docs/decisions      ADRs — write one for every irreversible choice
```

---

## 5. Data stack

Verify current pricing at each provider before subscribing. Prices below checked 2026-07.

### Odds (backbone: +EV engine, CLV, closing lines)

| Provider | Role | Tier / price | Notes & gotchas |
|---|---|---|---|
| TheRundown (therundown.io) | Primary odds + sharp reference | Free: 20K data points/day, 3 books. Starter $49/mo: all books + live. Pro $149/mo: ~30s updates | Includes Pinnacle + LowVig (sharp refs), Matchbook exchange, Kalshi/Polymarket. WebSocket on higher tiers. |
| OddsPapi (oddspapi.io) | Esports odds + backup | Free tier (~250 req/mo, each returns all books); custom above | Claims 350+ books incl. Pinnacle, GG.BET, Thunderpick; free historical line movement. Newer provider — claims are their own marketing. VALIDATE reliability before depending on it. |
| The Odds API (the-odds-api.com) | Optional supplement | Free 500 credits/mo; $30/20K; $59/100K; $119/5M | ~40 soft books, NO sharp books — insufficient alone for de-vig reference. Credits = markets × regions per call, burns fast. |

### Football stats (model features — never for lines)

| Provider | Tier / price | Notes |
|---|---|---|
| API-Football (api-football.com) | Free 100 req/day; Pro $19/mo 7,500/day; Ultra $29/mo 75K/day | 1,200+ leagues incl. Liga I & Eredivisie. All endpoints on all plans. **Odds endpoints refresh ~3h — never use for lines or CLV.** Daily quota resets 00:00 UTC. |
| football-data.org | Free: 12 competitions (top-5 leagues + UCL), 10 req/min | Good for prototyping; add-ons stack to €70+/mo — compare before buying. |
| Understat / FBref / StatsBomb open data | Free | xG for research & backtests only. Scraper-based sources are brittle — never in the production path. |

### Esports stats (CS2 model features)

| Provider | Tier / price | Notes |
|---|---|---|
| PandaScore (pandascore.co) | Free: 1,000 req/hr, schedules/results | **ToS TRAP: stats plans prohibit betting-related usage.** Betting use = custom sales plan. Paid stats are per-game: historical ~€400/mo/game, live ~€1,000+/mo/game. Use free tier for prototyping only; get written clarity before commercial launch. |
| GRID (grid.gg) | Developer/open-access program — apply | Official Valve CS2 data rights. Best long-term path for licensed CS2 data. |
| HLTV | — | **No official API. Do NOT scrape** — against ToS, actively enforced. Not a foundation for a business. |

Enterprise reference (Phase 3+ only): betting-grade multi-esport data (Sportradar,
Abios, PandaScore custom) runs roughly €500–1,500+/mo minimum, often $2K–10K/mo
with annual contracts. Subscription revenue must precede this cost.

### Monthly cost by phase

| Phase | Stack | ~Total |
|---|---|---|
| 0 — Validation (mo 0–3) | All free tiers + API-Football Pro $19 + Hetzner VPS €5 | **~€25/mo** |
| 1 — Public launch (mo 3–9) | TheRundown Starter $49 + API-Football Ultra $29 + OddsPapi free + Supabase Pro $25 + VPS €10 | **~€110–140/mo** |
| 2 — Scale | Odds tier $119–249 + licensed esports data €500–1,500 + deeper football (e.g. Sportmonks €99) + infra | **~€800–2,000/mo** |

---

## 6. Database schema (Postgres / Supabase)

Canonical DDL sketch — see /db/migrations for the source of truth. UTC everywhere.

```sql
create table sports (
  id text primary key,              -- 'cs2', 'football', 'dota2', ...
  name text not null
);

create table competitions (
  id uuid primary key default gen_random_uuid(),
  sport_id text not null references sports(id),
  name text not null,
  country text,
  tier smallint                     -- 1 = top flight / tier-1 events
);

create table teams (
  id uuid primary key default gen_random_uuid(),
  sport_id text not null references sports(id),
  canonical_name text not null,
  country text
);

create table team_aliases (
  team_id uuid not null references teams(id),
  provider text not null,           -- 'therundown', 'api_football', 'pandascore', ...
  provider_key text,                -- provider's own id if it has one
  alias text not null,              -- raw name string seen in payloads
  confidence numeric not null,      -- resolver score at creation
  verified boolean default false,   -- human-confirmed
  unique (provider, alias)
);

create table events (
  id uuid primary key default gen_random_uuid(),
  sport_id text not null references sports(id),
  competition_id uuid references competitions(id),
  home_team uuid references teams(id),
  away_team uuid references teams(id),
  commence_time timestamptz not null,
  status text not null default 'scheduled',  -- scheduled|live|finished|cancelled
  provider_keys jsonb not null default '{}'  -- {"therundown": "...", "api_football": "..."}
);

-- Biggest table by far. Monthly partitions. Also the backtest dataset.
create table odds_snapshots (
  id bigint generated always as identity,
  event_id uuid not null references events(id),
  bookmaker text not null,
  market text not null,             -- 'h2h', 'map_handicap', 'total_rounds', ...
  outcome text not null,
  price numeric not null,           -- decimal odds
  line numeric,                     -- handicap/total line if applicable
  captured_at timestamptz not null,
  is_closing boolean not null default false,
  primary key (id, captured_at)
) partition by range (captured_at);

create table models (
  id text primary key,              -- 'cs2_ml_v3'
  sport_id text not null references sports(id),
  market text not null,
  version text not null,
  status text not null check (status in ('research','shadow','public','retired')),
  config jsonb not null default '{}',
  created_at timestamptz not null default now()
);

-- INSERT-ONLY. Immutability enforced below.
create table picks (
  id uuid primary key default gen_random_uuid(),
  model_id text not null references models(id),
  event_id uuid not null references events(id),
  market text not null,
  outcome text not null,
  price_at_publish numeric not null,
  bookmaker text not null,
  stake_units numeric not null,     -- quarter-Kelly, capped (see §8)
  published_at timestamptz not null default now(),
  rationale text,                   -- explainer agent output
  features_hash text                -- sha256 of the feature payload used
);

create table settlements (
  pick_id uuid primary key references picks(id),
  result text not null check (result in ('win','loss','push','void')),
  closing_price numeric,            -- no-vig sharp close for the picked outcome
  clv numeric,                      -- see §8 for formula
  pnl_units numeric not null,
  settled_at timestamptz not null default now()
);
```

Immutability enforcement (both mechanisms, not one):
```sql
revoke update, delete on picks from authenticated, anon;
create or replace function forbid_mutation() returns trigger as $$
begin raise exception 'picks are append-only'; end;
$$ language plpgsql;
create trigger picks_append_only
  before update or delete on picks
  for each row execute function forbid_mutation();
```
Optional proof layer (cheap, high trust value): nightly job exports the picks table
to a public append-only GitHub repo. History rewrites would be publicly visible.

---

## 7. Core interfaces (Python)

All workers are Python 3.12, fully type-hinted. These Protocols are the contract that
makes sports plug-ins. Do not add sport-specific branches outside adapter/feature modules.

```python
from typing import Protocol
from datetime import datetime

class SportAdapter(Protocol):
    sport_id: str
    def fixtures(self, since: datetime, until: datetime) -> list["Fixture"]: ...
    def results(self, since: datetime) -> list["Result"]: ...
    def stats(self, event_id: str) -> dict: ...   # raw provider stats, normalized keys

class FeatureModule(Protocol):
    sport_id: str
    def features(self, event_id: str, as_of: datetime) -> "FeatureVector":
        """MUST only use data timestamped strictly before `as_of`.
        The backtest harness audits this — see §10."""

class Model(Protocol):
    model_id: str
    def predict(self, fv: "FeatureVector") -> dict[str, float]:
        """outcome -> probability; probabilities sum to 1 within a market."""
```

### Entity resolver

`resolve(provider, provider_key, raw_name, sport_id, date_hint) -> team_id | ReviewItem`

- Exact match on (provider, provider_key) or verified alias -> return id.
- Else fuzzy match (rapidfuzz `token_set_ratio`) against canonical names + aliases:
  - score >= 92: auto-link, store alias with confidence, verified=false
  - 80–92: push to review queue (admin UI), do NOT ingest dependent rows until resolved
  - < 80: create ReviewItem as probable-new-team
- Event matching across providers: same sport + commence_time within ±36h + same
  resolved team pair -> merge into one `events` row, union `provider_keys`.
- Never guess silently. An unresolved entity blocks quietly and visibly, not wrongly.

---

## 8. Odds collection, closing lines, and the math

### Polling cadence (per event, per collector)

| Time to kickoff | Frequency |
|---|---|
| > 48h | 1/day |
| 48–24h | 4/day |
| 24–2h | hourly |
| 2h–15m | every 5 min |
| final 15m | every 2 min (budget permitting) |

At `commence_time`, mark the last snapshot per (bookmaker, market, outcome, line)
as `is_closing = true`.

### Sharp reference & de-vig

- Primary sharp reference: Pinnacle (via TheRundown). Fallback: median of the
  3 sharpest available books, each de-vigged first.
- De-vig (multiplicative, start here): `p_i = (1/o_i) / sum_j(1/o_j)`
- Later upgrade: Shin's method for markets with favourite–longshot bias (football 1X2,
  outrights). Write an ADR when switching; keep both implementations comparable.

### Edge, staking, CLV

- Expected value: `EV = p_true * o_offered - 1`
- Layer-1 publish threshold: `EV >= 2.5%` after a margin haircut (config, not code).
- Kelly fraction: `f* = (p*o - 1) / (o - 1)`; publish **quarter-Kelly**, hard cap 2.0
  stake_units per pick. Never publish full Kelly.
- CLV: `clv = price_at_publish / novig_closing_price - 1` (store as decimal, display %).
  Also report probability-space delta in analytics. CLV is the primary model KPI —
  win rate is a vanity metric at small N.

---

## 9. Model lifecycle & validation gates — PRE-REGISTERED

Written before seeing any results. Changing these after observing performance is
forbidden and defeats the entire trust thesis.

- `research -> shadow`: walk-forward backtest over >= 2 seasons/eras of purchased or
  captured historical odds; leakage checklist (§10) passes; ADR written.
- `shadow -> public`: >= 150 shadow picks AND mean CLV > 0 with a 90% bootstrap CI
  excluding 0 AND no data-integrity incidents during shadow.
- `public -> retired`: trailing-200-pick CLV CI entirely below 0, OR upstream data
  source degraded, OR leakage discovered (retire immediately, disclose publicly).
- Report always: N, CLV distribution, flat-stake ROI, per-league splits, calibration
  plot. Never headline win% alone.

## 10. Backtest harness rules (leakage guards)

1. Walk-forward splits only. No random shuffles, ever.
2. Features are built via `FeatureModule.features(event_id, as_of=snapshot.captured_at)`
   — i.e., as of publish-time, not kickoff. The harness must query the world as the
   model would have seen it.
3. Odds in backtests are real captured/purchased historical prices for a named
   bookmaker. Never averages, never closing prices as if they were open prices.
4. Any table joined into features must carry an ingestion timestamp; the harness
   asserts `ingested_at < as_of` on every joined row (this is the leakage audit).
5. Backtest outputs are artifacts: stored with model config hash, data range, and
   git SHA, so every public claim is reproducible.

---

## 11. Agent layer (Claude API)

- `news_watcher`: polls roster/injury/news sources per sport; when `news_time` is
  later than the last sharp-line move on an affected market, emit a stale-line
  candidate to the ADMIN review feed. Agents never auto-publish picks.
- `explainer`: input = pick + structured feature payload (JSON only); output = a
  3-sentence rationale stored in `picks.rationale`. The prompt must forbid inventing
  numbers not present in the payload; validate output against the payload before save.
- Model: `claude-sonnet-4-6` via the Messages API; run explainer in nightly batches.
  Verify current model names/pricing at https://docs.claude.com before changing.

---

## 12. Regulatory guardrails — read before ANY monetization change

Baseline posture: subscription analytics product, no wagering, no operator links.

- **Affiliate links to bookmakers = STOP and get legal advice first.**
  - Romania: affiliates earning from redirected players require an ONJN Class 2
    licence; promoting unlicensed operators carries fines (~50,000–100,000 lei) and
    ONJN is in active enforcement mode (blacklist includes Polymarket since late 2025).
  - Netherlands: untargeted gambling advertising banned since 2023 — the affiliate/
    tipster ad route toward NL users is effectively closed.
  - If affiliates are ever added: geo-gate per country, licensed operators only,
    written legal opinion on file first.
- No influencer/celebrity gambling promotion in Romania (2025 audiovisual rule) —
  relevant to any future sponsored content.
- Marketing copy: no profit guarantees (see §2), 18+ everywhere, responsible-gambling
  resources linked. CI greps banned strings in /apps/web and bot templates.
- Keep the product wager-free (§2.6). This is a licensing boundary, not a preference.

---

## 13. Roadmap

- **Phase 0 (wk 1–2):** repo scaffold, Postgres schema + migrations, TheRundown/
  OddsPapi collectors live on Hetzner (snapshots start accruing day one — this is
  also the future backtest dataset), CI with immutability + banned-strings checks.
- **Phase 1 (wk 3–6):** entity resolver + review queue, football adapter
  (API-Football), +EV engine, minimal Next.js site with live +EV board.
- **Phase 2 (wk 6–12):** CS2 feature module + model v1 into `shadow`; Telegram
  publishing; public track-record page (per-model N, ROI, CLV, equity curve, full
  pick log including losses); explainer agent.
- **Phase 3 (mo 3–6):** evaluate gates -> first `public` model; free verified picks
  as the growth loop; Liga I / Eredivisie model into shadow; build-in-public posting.
- **Phase 4 (mo 6+):** paywall (€12–15/mo) once a public model has a defensible
  record; apply GRID / licensed esports data; explore B2B probability feed.

---

## 14. Conventions for Claude Code sessions in this repo

- Python 3.12, `uv` for deps, `ruff` for lint/format, `pytest`. Type hints mandatory.
- Workers are idempotent — any job can be re-run safely; upserts keyed on natural keys.
- Store UTC everywhere; convert only at the UI edge.
- Secrets in `.env` (never committed); provider keys named `{PROVIDER}_API_KEY`.
- Never invent provider response fields. Check `/docs/providers/*.md`; when adding a
  provider, save a raw sample payload to `/docs/providers/samples/` in the same PR.
- Migrations only via Supabase CLI in `/db/migrations`; never edit applied migrations.
- Any change touching `picks` or `settlements` must include a test proving the
  append-only guarantees still hold.
- Every irreversible choice (provider contract, de-vig method change, gate change —
  which should never happen, see §9) gets an ADR in `/docs/decisions`.
- When asked to "add a sport": implement SportAdapter + FeatureModule + alias seeds +
  docs page. If the request implies touching shared plumbing, stop and flag it.
