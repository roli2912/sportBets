# OddsPapi (oddspapi.io)

Role: **primary esports odds source** (upgraded from "backup" — verified
2026-07-11 that the free tier returns live Pinnacle CS2 odds, which TheRundown
cannot provide at any tier). Also a candidate backup for football.

> WARNING (from CLAUDE.md): newer provider — reliability still needs the
> validation plan below before it enters the production path.

Verified against the live v4 API on 2026-07-11.

## Plan

| Tier | Price | Notes |
|---|---|---|
| Free | $0 | ~250 req/mo — Phase 0/1. NOTE: odds requests are per-bookmaker (see quirks), so "each request returns all books" does NOT hold for `/odds-by-tournaments`. |
| Custom | contact | above free volume |

## Auth

- Env var: `ODDSPAPI_API_KEY`.
- Query param: `?apiKey=<key>` on every request.
- Base URL: `https://api.oddspapi.io/v4`.

## Endpoints (used by the collector)

| Endpoint | Notes |
|---|---|
| `GET /sports` | 10=Soccer, 16=Dota 2, 17=CS2, 61=Valorant; sample: `samples/oddspapi_sports.json` |
| `GET /tournaments?sportId=17` | CS2: 325 tournaments; rows carry LIVE counts (`futureFixtures`/`upcomingFixtures`/`liveFixtures`) — the collector discovers active tournaments from these each poll (cap: `ODDSPAPI_MAX_TOURNAMENTS`, default 2); sample: `samples/oddspapi_tournaments_cs2.json` |
| `GET /fixtures?tournamentId={id}` | **singular** param; includes participant names/status; sample: `samples/oddspapi_fixtures_cs2_blast.json` |
| `GET /odds-by-tournaments?tournamentIds={csv}&bookmaker={one}` | **exactly one bookmaker per request**; sample: `samples/oddspapi_odds_cs2_blast.json` |
| `GET /markets` | marketId reference (~9MB raw); trimmed copy for sports 10/16/17/61: `samples/oddspapi_markets.json` |

## Payload shape (odds)

`[] -> bookmakerOdds{book} -> markets{marketId} -> outcomes{outcomeId} -> players{"0"} -> {price, mainLine, active, limit, ...}`

- Prices are **decimal** already; `limit` = Pinnacle stake limit; `mainLine`
  flags the main handicap/total.
- Odds fixtures carry `participant1Id`/`participant2Id` but **no names** —
  names come from `/fixtures` (`participant1Name` = home, `participant2Name`
  = away).
- Market semantics live in the `/markets` reference:
  `{marketId, marketType, period, handicap, outcomes[{outcomeId, outcomeName}]}`.
  Same `marketType` repeats per handicap value (e.g. one totals marketId per
  line: 173 = maps O/U 2.5, 175 = maps O/U 3.5, ...).

## Market mapping (collector `_MARKET_TYPE_MAP`)

| sport | marketType/period | canonical |
|---|---|---|
| esports | moneyline / result | `h2h` |
| esports | totals / result | `total_maps` |
| esports | spreads / result | `map_handicap` |
| soccer | 1x2 / fulltime | `h2h` |
| soccer | totals / fulltime | `totals` |
| soccer | spreads / fulltime | `asian_handicap` |
| soccer | bothteamsscore / fulltime | `btts` |

Per-map markets (period `p1`..`p5`) are skipped for now. Outcome names from
the reference: `1`->home, `2`->away, `X`->draw, Over/Under, Yes/No.

## Quirks (all verified 2026-07-11)

- `/odds-by-tournaments` returns 400 ("Invalid number of bookmakers") unless
  EXACTLY ONE `bookmaker` param is given → every extra book costs one request
  from the ~250/mo budget. Keep the bookmaker list short (pinnacle + 1 soft).
- `/fixtures` requires the SINGULAR `tournamentId`; `tournamentIds` → 400
  ("Missing parameters").
- `/markets` is ~9MB — fetch once, cache/save; the collector accepts an
  injected `markets_ref` to avoid re-fetching.
- Timestamps ISO-8601 with `Z` and milliseconds.
- `bookmakerOutcomeId` strings like `"2.5/over"` / `"-1.5/home"` duplicate
  the reference handicap — the collector uses the reference, not the string.
- `/odds-by-tournaments` returns **404 once a tournament has finished / has no
  active odds** (observed 2026-07-12, the day after BLAST 31621 ended, id was
  valid the day before). Hence: (a) the collector discovers active tournaments
  from `/tournaments` live counts instead of pinning ids, and (b) the scheduler
  stamps `last_poll` on 4xx so a stale id can never retry-loop every 60s tick.
- Budget math with discovery (12h floor, 2 tournaments, 2 bookmakers):
  1 tournaments + 2 fixtures + 2 odds = 5 req/poll × 2/day ≈ 300/mo worst case
  vs ~250 free — only when 2 tournaments run non-stop all month; quiet weeks
  cost 2/day. Watch the meter; drop to 1 bookmaker if it gets tight.

## Validation plan (before it enters the production path)

- [x] Free tier returns live Pinnacle CS2 odds with limits (2026-07-11)
- [ ] Compare Pinnacle prices vs another source for the same events (staleness)
- [ ] Measure update latency around kickoff for CS2 tier-1 matches
- [ ] Confirm historical line-movement export actually exists on free tier
- [ ] 2-week uptime observation logged here

## Implementation status

`workers/collectors/oddspapi.py` — implemented 2026-07-11; tests in
`workers/tests/test_collectors_parsing.py` pinned to the saved samples.
