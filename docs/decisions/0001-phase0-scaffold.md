# ADR 0001 — Phase 0 scaffold choices

Date: 2026-07-10
Status: accepted

## Context

Phase 0 (CLAUDE.md §13) requires: repo scaffold, Postgres schema + migrations,
odds collectors, CI with immutability + banned-strings checks. Solo developer;
modular monolith mandated (§3).

## Decisions

1. **Monorepo layout per CLAUDE.md §4**, npm workspaces at root for future
   `apps/web` + `packages/*`; `/workers` is a standalone Python 3.12 project
   managed with `uv`, packages named exactly as §4 (`collectors`, `engine`, ...).
2. **Raw SQL via psycopg 3, no ORM.** The schema is small, the hot path is
   bulk inserts into a partitioned table, and immutability is enforced in the
   DB itself — an ORM adds abstraction over exactly the layer we need to keep
   explicit.
3. **De-vig starts multiplicative** (`p_i = (1/o_i)/Σ(1/o_j)`) per §8. Shin's
   method is a future ADR when football 1X2/outrights models arrive.
4. **Stake unit convention: 1 unit = 1% of bankroll** (`bankroll_units = 100`).
   Quarter-Kelly output expressed in these units, hard cap 2.0 units (§8).
5. **Closing-line capture lives in SQL** (`mark_closing_lines(event)` function)
   so the definition has one home, callable from any worker and testable in CI.
6. **Provider parsers ship blocked** (`NotImplementedError`) until raw sample
   payloads are committed to `/docs/providers/samples/` — enforcing §14's
   "never invent provider response fields" structurally.
7. **CI enforces the trust layer**: pytest runs the picks append-only test
   against a real Postgres service with migrations applied; a banned-strings
   grep guards public copy (§2.4).

## Consequences

- Collectors cannot ingest until keys + samples exist (deliberate; see docs
  pages for the unblock checklists).
- Any future ORM/query-builder adoption needs a new ADR.
