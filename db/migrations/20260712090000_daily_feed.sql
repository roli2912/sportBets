-- 20260712090000_daily_feed.sql
--
-- Daily Best Bets feed (pivot ADR 0002; CLAUDE.md §13 Task 2).
-- One frozen selection per UTC day, referencing already-immutable picks.
-- The feed is part of the public record: once a day's feed exists it is never
-- edited (§2.1 spirit), so "what did you show on date X" is always answerable.
--
-- label semantics (§9 visibility-vs-badge):
--   'verified'    = pick from a model with status 'public' (gate-passed) —
--                   the ONLY label allowed to carry the verified badge;
--   'market_edge' = Layer-1 market-model signal (status 'shadow'), clearly
--                   labeled as unverified. The two must never blur.

create table if not exists daily_feed (
  feed_date  date not null,
  rank       smallint not null check (rank >= 1),
  pick_id    uuid not null references picks(id),
  label      text not null check (label in ('verified', 'market_edge')),
  created_at timestamptz not null default now(),
  primary key (feed_date, rank),
  unique (feed_date, pick_id)
);

-- Append-only: same double enforcement as picks (§6) — revoke + trigger.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on daily_feed from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on daily_feed from authenticated;
  end if;
end $$;

create or replace function daily_feed_forbid_mutation() returns trigger as $$
begin
  raise exception 'daily_feed is append-only';
end;
$$ language plpgsql;

drop trigger if exists daily_feed_append_only on daily_feed;
create trigger daily_feed_append_only
  before update or delete on daily_feed
  for each row execute function daily_feed_forbid_mutation();

alter table daily_feed enable row level security;
