-- 20260712120000_outbound_posts.sql
--
-- Delivery log for outbound publishing (Telegram now; Discord later).
-- Records what was already sent so publisher re-runs are no-ops (§14:
-- workers are idempotent). This is NOT a trust surface — picks and
-- settlements are; this table only prevents duplicate messages.

create table if not exists outbound_posts (
  id        bigint generated always as identity primary key,
  channel   text not null,                                   -- 'telegram'
  kind      text not null check (kind in ('pick', 'digest')),
  pick_id   uuid references picks(id),
  feed_date date,
  sent_at   timestamptz not null default now(),
  check (pick_id is not null or feed_date is not null)
);

-- One post per pick per channel; one digest per feed date per channel.
create unique index if not exists outbound_posts_pick_uniq
  on outbound_posts (channel, kind, pick_id) where pick_id is not null;
create unique index if not exists outbound_posts_digest_uniq
  on outbound_posts (channel, kind, feed_date) where feed_date is not null;

alter table outbound_posts enable row level security;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on outbound_posts from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on outbound_posts from authenticated;
  end if;
end $$;
