# TheRundown (therundown.io)

Role: **primary odds source** for US-major + top-flight soccer markets.
See CLAUDE.md §5 — but note the verified coverage/sharp-book caveats below,
which materially change the plan.

Verified against the live v2 API on 2026-07-11.

## Plan

| Tier | Price | Notes |
|---|---|---|
| Free | $0 | 20K data points/day, 3 books (DraftKings, BetMGM, FanDuel) — **no sharp book** |
| Starter | $49/mo | all books + live — **required for Pinnacle (sharp reference)** |
| Pro | $149/mo | ~30s updates, WebSocket — Phase 2 |

Verify current pricing before subscribing (last checked 2026-07).

## Coverage gaps (verified 2026-07-11 via /sports)

- **NO esports** (no CS2/Dota/Valorant sports at all).
- **NO Liga I (Romania), NO Eredivisie.** Soccer = EPL, Ligue 1, Bundesliga,
  La Liga, Serie A, UCL, UEL, Euro, World Cup, MLS, Liga MX, J1.
- Consequence: TheRundown cannot serve the CS2 or soft-football-league
  verticals. OddsPapi is the esports path; a football odds source for Liga I /
  Eredivisie is still an open question.

## Auth

- Env var: `THERUNDOWN_API_KEY`.
- Header: `X-TheRundown-Key: <key>`.
- Base URL: `https://therundown.io/api/v2`.

## Endpoints (used by the collector)

| Endpoint | Notes |
|---|---|
| `GET /sports` | sport list; sample: `samples/therundown_sports.json` |
| `GET /sports/{sport_id}/events/{YYYY-MM-DD}` | events + markets + prices in one response; sample: `samples/therundown_events_fifa.json` |
| `GET /affiliates` | affiliate_id -> bookmaker name; free reference call; sample: `samples/therundown_affiliates.json` |

## Payload shape (events)

`events[] -> markets[] -> participants[] -> lines[] -> prices{affiliate_id: {...}}`

- `teams_normalized[]` with `is_home` / `is_away` booleans (NOT list order).
- `markets[].market_id`: 1 = moneyline, 2 = handicap, 3 = totals;
  `period_id` 0 = full time.
- Participants: `TYPE_TEAM` (match `id` against `teams[].team_id` for
  home/away) or `TYPE_RESULT` (`Draw`, `Over`, `Under`).
- `lines[].value` = handicap/total line (string like `"+0.5"` for handicaps).
- `prices` values: `{price, is_main_line, ...}` — **American odds**; the
  collector converts to decimal at the parse boundary.

## Bookmaker IDs seen on free tier

| affiliate_id | book |
|---|---|
| 19 | DraftKings |
| 22 | BetMGM |
| 23 | FanDuel |
| 3 | Pinnacle — **Starter plan only, NOT on free tier** |

## Implementation status

`workers/collectors/therundown.py` — implemented 2026-07-11.

- [x] API key provisioned, auth mechanics documented above
- [x] Raw sample payloads saved to `samples/therundown_*.json`
- [x] Sport IDs mapping (`SPORT_MAP` in collector; soccer only for now)
- [x] Market -> canonical taxonomy: 1->`h2h`, 2->`asian_handicap`, 3->`totals`
- [x] Sharp books: Pinnacle = affiliate 3 — **paywalled behind Starter**

## Quirks

- Free tier has no sharp book, so TheRundown alone cannot feed the de-vig
  reference. Either subscribe to Starter ($49/mo) or use another source for
  the sharp side.
- Totals sample carried many non-main lines (`is_main_line: false`); the
  collector defaults to `main_lines_only=True`.
- `event_date` is ISO-8601 with `Z`; handled by `datetime.fromisoformat`.
- Data-point accounting on the free tier (20K/day) is per data point, not per
  request — one events call with many markets/books burns many points.
