-- 20260711180000_results_and_pick_dedupe.sql
--
-- Phase 2 groundwork: grading needs match results, and the Layer-1 shadow
-- publisher needs picks to be idempotently insertable (CLAUDE.md §14: workers
-- are re-runnable; §2: picks stay INSERT-only).

-- 1) Results, provider-attributed and ingestion-timestamped (§10.4: any table
--    joined into features/grading carries an ingestion timestamp). One result
--    per event; the first provider to report wins — results never mutate.
create table if not exists event_results (
  event_id    uuid primary key references events(id),
  provider    text not null,
  home_score  int not null,
  away_score  int not null,
  finished_at timestamptz,
  ingested_at timestamptz not null default now()
);
alter table event_results enable row level security;

-- 2) picks.line — a totals/handicap pick is ambiguous without its line
--    (outcome 'over' means nothing without 2.5). Additive and nullable;
--    append-only enforcement (trigger + revokes) is untouched.
alter table picks add column if not exists line numeric;

-- 3) Idempotent publishing: one pick per (model, event, market, outcome, line).
--    Re-runs become no-ops via INSERT ... ON CONFLICT DO NOTHING — never an
--    update. Bookmaker is intentionally NOT in the key: the publisher takes the
--    best-EV book for an outcome, and one track-record entry per outcome.
create unique index if not exists picks_publish_uniq
  on picks (model_id, event_id, market, outcome, (coalesce(line, 'NaN'::numeric)));

-- 4) Full feature payload per pick (not just the hash): §10 audits and the
--    §11 explainer both need the exact numbers the publisher saw. Append-only
--    by usage; RLS like everything else.
create table if not exists pick_features (
  pick_id     uuid primary key references picks(id),
  payload     jsonb not null,
  ingested_at timestamptz not null default now()
);
alter table pick_features enable row level security;

-- 5) §11 stores explainer output in picks.rationale, but the §6 trigger
--    forbade ALL updates. §2.1 deliberately lists the immutable fields —
--    rationale is not one of them. Narrow exception: a NULL rationale may be
--    backfilled ONCE; every other field (and non-null rationale) stays locked.
create or replace function picks_rationale_backfill_only() returns trigger as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'picks are append-only';
  end if;
  if new.id               is distinct from old.id
     or new.model_id         is distinct from old.model_id
     or new.event_id         is distinct from old.event_id
     or new.market           is distinct from old.market
     or new.outcome          is distinct from old.outcome
     or new.line             is distinct from old.line
     or new.price_at_publish is distinct from old.price_at_publish
     or new.bookmaker        is distinct from old.bookmaker
     or new.stake_units      is distinct from old.stake_units
     or new.published_at     is distinct from old.published_at
     or new.features_hash    is distinct from old.features_hash
     or old.rationale is not null
  then
    raise exception 'picks are append-only (only a NULL rationale may be backfilled)';
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists picks_append_only on picks;
create trigger picks_append_only
  before update or delete on picks
  for each row execute function picks_rationale_backfill_only();
