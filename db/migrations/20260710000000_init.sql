-- 20260710000000_init.sql
-- Initial schema. Canonical DDL per CLAUDE.md §6. UTC everywhere.
-- NEVER edit this file after it has been applied — add a new migration instead.

-- ---------------------------------------------------------------------------
-- Reference tables
-- ---------------------------------------------------------------------------

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

create index events_commence_time_idx on events (commence_time);
create index events_provider_keys_gin on events using gin (provider_keys jsonb_path_ops);

-- ---------------------------------------------------------------------------
-- Odds snapshots — biggest table by far; monthly partitions; backtest dataset
-- ---------------------------------------------------------------------------

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

create index odds_snapshots_event_idx on odds_snapshots (event_id, captured_at);
create index odds_snapshots_closing_idx on odds_snapshots (event_id) where is_closing;

-- Partition maintenance: call for current + next month from a scheduled job.
create or replace function ensure_odds_snapshots_partition(p_day date) returns text as $$
declare
  p_start date := date_trunc('month', p_day)::date;
  p_stop  date := (p_start + interval '1 month')::date;
  p_name  text := 'odds_snapshots_' || to_char(p_start, 'YYYY_MM');
begin
  execute format(
    'create table if not exists %I partition of odds_snapshots for values from (%L) to (%L)',
    p_name, p_start, p_stop
  );
  return p_name;
end;
$$ language plpgsql;

select ensure_odds_snapshots_partition(current_date);
select ensure_odds_snapshots_partition((current_date + interval '1 month')::date);

-- Closing-line capture (§8): at commence_time, mark the last snapshot per
-- (bookmaker, market, outcome, line) as closing. Idempotent.
create or replace function mark_closing_lines(p_event uuid) returns integer as $$
declare
  v_count integer;
begin
  with latest as (
    select distinct on (bookmaker, market, outcome, coalesce(line, 'NaN'::numeric))
           id, captured_at
    from odds_snapshots
    where event_id = p_event
      and captured_at <= (select commence_time from events where id = p_event)
    order by bookmaker, market, outcome, coalesce(line, 'NaN'::numeric), captured_at desc
  )
  update odds_snapshots o
     set is_closing = true
    from latest l
   where o.id = l.id
     and o.captured_at = l.captured_at
     and not o.is_closing;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$ language plpgsql;

-- ---------------------------------------------------------------------------
-- Models & picks
-- ---------------------------------------------------------------------------

create table models (
  id text primary key,              -- 'cs2_ml_v3'
  sport_id text not null references sports(id),
  market text not null,
  version text not null,
  status text not null check (status in ('research','shadow','public','retired')),
  config jsonb not null default '{}',
  created_at timestamptz not null default now()
);

-- INSERT-ONLY. Immutability enforced below (§2.1, §6).
create table picks (
  id uuid primary key default gen_random_uuid(),
  model_id text not null references models(id),
  event_id uuid not null references events(id),
  market text not null,
  outcome text not null,
  price_at_publish numeric not null,
  bookmaker text not null,
  stake_units numeric not null,     -- quarter-Kelly, capped (§8)
  published_at timestamptz not null default now(),
  rationale text,                   -- explainer agent output
  features_hash text                -- sha256 of the feature payload used
);

create table settlements (
  pick_id uuid primary key references picks(id),
  result text not null check (result in ('win','loss','push','void')),
  closing_price numeric,            -- no-vig sharp close for the picked outcome
  clv numeric,                      -- clv = price_at_publish / novig_closing_price - 1
  pnl_units numeric not null,
  settled_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Immutability enforcement — BOTH mechanisms, per CLAUDE.md §6
-- ---------------------------------------------------------------------------

-- 1) Privilege revocation (Supabase roles; guarded so vanilla Postgres CI works)
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke update, delete on picks from authenticated;
  end if;
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke update, delete on picks from anon;
  end if;
end $$;

-- 2) Trigger — blocks every role including table owner paths via normal DML
create or replace function forbid_mutation() returns trigger as $$
begin raise exception 'picks are append-only'; end;
$$ language plpgsql;

create trigger picks_append_only
  before update or delete on picks
  for each row execute function forbid_mutation();

-- ---------------------------------------------------------------------------
-- Seeds
-- ---------------------------------------------------------------------------

insert into sports (id, name) values
  ('cs2', 'Counter-Strike 2'),
  ('football', 'Football (Soccer)')
on conflict (id) do nothing;
