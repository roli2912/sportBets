# PandaScore (pandascore.co)

Role: **CS2 fixtures + results** (stats side — grading the esports picks).
Never used for odds/lines (§5). Verified against the live API on 2026-07-12.

> **ToS TRAP (§5):** stats plans prohibit betting-related usage; betting use
> requires a custom sales plan. The free tier is used for PROTOTYPING ONLY.
> Get written clarity from PandaScore before any commercial launch.

## Plan

| Tier | Price | Notes |
|---|---|---|
| Free | $0 | 1,000 req/hr; schedules/results only (no detailed stats) |
| Historical / Live | ~€400 / €1,000+ per game/mo | betting usage still needs custom terms |

## Auth

- Env var: `PANDASCORE_API_KEY`.
- Header: `Authorization: Bearer <key>`.
- Base URL: `https://api.pandascore.co`.

## Endpoints (used by the adapter)

| Endpoint | Notes |
|---|---|
| `GET /csgo/matches/upcoming?per_page=N` | future matches, `status: not_started`; sample: `samples/pandascore_matches_upcoming_cs2.json` |
| `GET /csgo/matches/past?per_page=N` | finished matches, newest first, `status: finished`; sample: `samples/pandascore_matches_past_cs2.json` |

CS2 still lives under the legacy `csgo` path — the samples above are current
CS2 fixtures fetched from it.

## Payload shape (matches)

- `opponents[] -> {opponent: {id, name, ...}, type: "Team"}` — exactly 2 for
  normal matches; fewer when a bracket slot is TBD (adapter skips those).
- `results[] -> {score, team_id}` — matched to opponents via `team_id`,
  NOT by position.
- `winner_id`, `draw`, `forfeit`, `match_type` ("best_of"), `number_of_games`.
- League/serie/tournament nesting: `league.name`, `serie.full_name`,
  `tournament.name`.

## Quirks (verified 2026-07-12)

- **No home/away in esports.** `opponents` order is PandaScore's own and does
  not match other providers (OddsPapi participant1 ≠ opponents[0] in general).
  The adapter puts names on every `Result`; `persist_results` aligns scores to
  the event's resolved orientation via `team_aliases` and skips (never
  guesses) when a name is unresolved. Blind positional writes would flip
  ~50% of merged events' scores.
- Timestamps can be **null even on finished matches**: `begin_at` falls back
  to `scheduled_at` then `original_scheduled_at`, and one past-sample row has
  all three null. Such results are still returned by the adapter (no window
  filter possible); `persist_results` only attaches events already ingested.
- Draws are real (BO2 can end 1-1) — kept; h2h grades them as a push.
- Forfeits still carry a score/winner — kept, `raw_status = "forfeit"`.
- Timestamps ISO-8601 with `Z`.
- Budget: free tier 1,000 req/hr vs 2 req/poll (upcoming + past) at a
  multi-hour cadence — no realistic pressure.

## Implementation status

`workers/adapters/cs2.py` — implemented 2026-07-12; parser tests pinned to the
saved samples in `workers/tests/test_collectors_parsing.py`; orientation
alignment tested in `workers/tests/test_results_orientation.py`.
