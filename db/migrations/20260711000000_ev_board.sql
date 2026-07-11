-- 20260711000000_ev_board.sql
-- Derived cache for the live +EV board (Layer 1, CLAUDE.md §1, §8).
-- NOT part of the immutable record: rebuilt wholesale by engine/board.py on
-- each refresh. The web app only reads it.

create table ev_board (
  id bigint generated always as identity primary key,
  event_id uuid not null references events(id),
  bookmaker text not null,          -- soft book offering the price
  market text not null,
  outcome text not null,
  line numeric,
  price numeric not null,           -- soft price (decimal odds)
  sharp_price numeric not null,     -- sharp book's raw price for same outcome
  p_true numeric not null,          -- de-vigged sharp probability
  novig_price numeric not null,     -- fair price = 1 / p_true
  ev numeric not null,              -- p_true * price - 1 (raw, pre-haircut)
  captured_at timestamptz not null, -- when the soft price was observed
  computed_at timestamptz not null default now()
);

create index ev_board_event_idx on ev_board (event_id);
create index ev_board_ev_idx on ev_board (ev desc);
