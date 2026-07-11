# API-Football (api-football.com / api-sports.io)

Role: **football stats side only** — fixtures, results, later team/player stats
for FeatureModules. NEVER a source of betting lines or CLV (see quirks).

Verified live on 2026-07-11 (Pro plan, direct api-sports.io key).

## Auth

- Header: `x-apisports-key: $API_FOOTBALL_API_KEY`
- Base URL (direct, not RapidAPI): `https://v3.football.api-sports.io`

## Plan / quota

- Pro $19/mo: 7,500 req/day (free tier: 100/day). Daily quota resets 00:00 UTC.
- All endpoints available on all plans; only quota differs.

## Endpoints we use (verified)

| Endpoint | Params verified | Sample |
|---|---|---|
| `GET /fixtures` | `league=283&season=2026&next=5` (upcoming) | `samples/api_football_fixtures_liga1.json` |
| `GET /fixtures` | `league=283&season=2025&last=5` (finished) | `samples/api_football_results_liga1.json` |
| `GET /leagues` | `country=Romania` | `samples/api_football_leagues_ro.json` |

League ids: **Liga I = 283** (Romania). Eredivisie id not yet captured — verify
before use.

## Response envelope

Every response is HTTP 200 with:

```json
{ "get": "fixtures", "parameters": {...}, "errors": [], "results": 5,
  "paging": {"current": 1, "total": 1}, "response": [ ... ] }
```

- **Errors arrive as HTTP 200 + non-empty `errors`** (list or object). The
  adapter raises on any non-empty `errors`.
- `response[]` items: `fixture` (id, date ISO-8601 UTC, status), `league`
  (id, name, season, round), `teams.home/.away` (id, name, winner),
  `goals` (final incl. extra time), `score` (halftime/fulltime/extratime/penalty).

## Quirks / gotchas

- **Odds endpoints refresh only ~every 3h — never use for lines or CLV**
  (CLAUDE.md §5). Odds come from TheRundown/OddsPapi collectors exclusively.
- Finished statuses observed: `FT`, `AET`, `PEN`.
  - `goals` is the FINAL score including extra time (AET sample: goals 4-3,
    fulltime 3-3). `score.penalty` holds the shootout only.
  - 1X2/h2h and totals settle on the 90-minute score ⇒ the adapter's `Result`
    carries **`score.fulltime`**, never `goals`.
- Upcoming fixtures use `next=N` / finished use `last=N`; `from`/`to` window
  params are NOT verified against samples — do not use without capturing one.
- Team names are Romanian-diacritic-free in Liga I payloads (e.g.
  "Farul Constanta") — the entity resolver's alias table absorbs provider
  spelling differences.

## Adapter

`workers/adapters/football.py` — `ApiFootballAdapter(league_ids, season)`:
- `fixtures(since, until)`: `next=50` per league, window-filtered client-side.
- `results(since)`: `last=50` per league, FT/AET/PEN only, 90-min scores.
- `stats()`: NotImplementedError until a statistics sample is captured (§14).
- 429 handling: 3 attempts, honors `Retry-After`, sleep capped at 60s.
