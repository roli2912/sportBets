# ADR 0002 — Pivot to a multi-sport daily betting research app

Date: 2026-07-12
Status: accepted (strategic decision made by the founder; recorded here, not relitigated)

## Context

The original positioning (CLAUDE.md pre-2026-07-12) treated CS2 as the flagship
vertical, with the multi-sport market engine as background plumbing. Observations
after Phases 0–2:

- Layer 1 (+EV engine) already produces a daily, multi-sport stream of signals
  with zero per-sport modeling cost — it is the natural public surface.
- A single-esport flagship concentrates brand risk on one game, one data
  provider (PandaScore ToS restricts betting usage on paid stats), and a niche
  audience, while the collectors already cover many sports.
- Trust ("radical verification") compounds fastest with daily visible output,
  not with a model that publishes a few picks per week.

## Decision

1. **Public identity = the multi-sport Daily Best Bets feed.** Positioning:
   "Your daily betting research desk for European sports — every pick explained,
   every result public." No single sport carries the brand.
2. **CS2 and Liga I football are the first two Layer-2 verticals**, not a
   flagship + follower. Each earns the "verified" badge only via the §9 gates.
3. **Visibility vs badge (§9 amendment):** all picks are publicly visible from
   day one, labeled by model status. The pre-registered gates now control
   (a) the "verified" badge and (b) entry into the Daily Best Bets feed —
   not raw visibility. **Gate thresholds are unchanged** (this is an additive
   clarification of what passing a gate grants, not a change to the criteria —
   the §9 change-freeze applies to thresholds).
4. **§13 roadmap replaced** by Tasks 2–7 (daily feed, backtest harness, Telegram
   + proof export, football model, CS2 model, SEO scaffold) plus a deferred
   list (news_watcher, Shin de-vig, admin web UI, extra providers).
5. **New §15 GTM:** free daily pick → €9.99–14.99/mo premium (annual = 2 months
   free); organic channels only (SEO, Telegram/Discord, short-form video,
   build-in-public); launch mid-August 2026 with the European club season.

## What does NOT change

- §2 non-negotiables: pick immutability, CLV grading, pre-registered gates,
  no profit-guarantee language, 18+ everywhere, no wagering.
- Architecture (§3): sport-agnostic plumbing + SportAdapter/FeatureModule
  plug-ins. The pivot is identity/sequencing/surface, not structure.
- §9 gate thresholds (>=150 shadow picks, CLV CI excluding 0, etc.).

## Consequences

- Homepage becomes the Daily Best Bets feed (Task 2); two label kinds
  ("verified" model pick vs market-edge signal) must never blur — enforced in
  templates and tested (workers/tests/test_publishing_templates.py).
- Compliance surface grew first (Task 0): footer on every route with CI render
  check, 18+ line in all bot templates, banned-strings lint extended to
  workers/publishing.
- news_watcher deferred post-launch (§11).
- Reversal cost: moderate (copy + homepage + roadmap docs), but public
  positioning and the launch date make this effectively one-way — hence ADR.

## Gate check at pivot time (2026-07-12, prod DB)

Per the pivot plan, the Layer-1 market model's shadow record was checked against
the unchanged §9 shadow->public gate:

| model | picks | settled w/ CLV | mean CLV | 90% CI | gate result |
|---|---|---|---|---|---|
| demo_ev_v0 | 4 | 4 | +0.0745 | (0.070, 0.079) | FAIL — N=4 < 150 |
| market_ev_v1_cs2 | 1 | 0 | — | — | FAIL — no settled CLV |
| market_ev_v1_football | 1 | 0 | — | — | FAIL — no settled CLV |

No model promoted. All remain `shadow`; their picks stay publicly visible as
labeled market-edge signals per the visibility-vs-badge rule.
