-- 20260711160000_supabase_rls.sql
--
-- Supabase hardening. A fresh Supabase project exposes every table in schema
-- public through PostgREST with grants to anon/authenticated. This platform's
-- workers and web app connect over the Postgres wire protocol as the table
-- owner, so the Data API surface is pure attack surface:
--   1) enable RLS on all public tables (no policies = deny for non-owners),
--   2) revoke API-role privileges outright (belt and braces, §2.1 spirit),
--   3) stop future default grants,
--   4) new odds_snapshots partitions get RLS at creation time.
-- Guarded so vanilla Postgres (local dev/CI without Supabase roles) works.

-- 1) RLS everywhere, including existing odds_snapshots partitions.
do $$
declare
  t record;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table public.%I enable row level security', t.tablename);
  end loop;
end $$;

-- 2 + 3) Revoke API-role access now and by default for future tables.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on all tables in schema public from anon;
    execute 'alter default privileges in schema public revoke all on tables from anon';
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on all tables in schema public from authenticated;
    execute 'alter default privileges in schema public revoke all on tables from authenticated';
  end if;
end $$;

-- 4) Partition maintenance now enables RLS on each new child table.
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
  execute format('alter table %I enable row level security', p_name);
  return p_name;
end;
$$ language plpgsql;
