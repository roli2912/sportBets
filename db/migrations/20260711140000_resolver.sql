-- Entity resolver support (CLAUDE.md §7).
--
-- 1) Events keep the RAW provider team names until the resolver links
--    canonical team ids. Collectors write them at creation; the resolver
--    fills home_team/away_team and (later) merges cross-provider duplicates.
-- 2) review_items is the human queue: fuzzy scores in 80-92 or probable new
--    teams land here. Unresolved entities block visibly, never wrongly.

alter table events
  add column home_name_raw text,
  add column away_name_raw text;

create table review_items (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('ambiguous_alias', 'new_team')),
  provider text not null,
  provider_key text,
  raw_name text not null,
  sport_id text not null references sports(id),
  candidate_team uuid references teams(id),  -- best fuzzy match, if any
  confidence numeric,                        -- resolver score 0..1, if any
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'rejected')),
  created_at timestamptz not null default now(),
  resolved_at timestamptz
);

-- Repeat sightings of the same unresolved name must not spam the queue.
create unique index review_items_pending_uniq
  on review_items (provider, raw_name, sport_id)
  where status = 'pending';
