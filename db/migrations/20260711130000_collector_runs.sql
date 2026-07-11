-- Scheduler bookkeeping: one row per odds provider, updated after each
-- successful poll cycle. Survives worker restarts so the cadence bands
-- (CLAUDE.md §8) and provider budget floors are respected across runs.

create table collector_runs (
  provider text primary key,
  last_polled_at timestamptz not null
);
