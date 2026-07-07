-- Jason memory system — Supabase schema
-- One table holds everything textual/structured, one row per customer
-- (media goes to Cloudflare R2, never here). Matches jason/supabase_store.py.
-- Run in the Supabase SQL editor (or via the Supabase MCP connector).

create schema if not exists jason_memory;
set search_path to jason_memory;

create table if not exists customers (
  email            text primary key,                     -- customer email; agency rows use the domain
  preferences      text  not null default '',            -- freeform durable preferences (profile notes)
  personal_details jsonb not null default '{}'::jsonb,   -- name, phone, agency pointer, defaults...
  past_jobs        jsonb not null default '[]'::jsonb,   -- array of job dicts, one item per job
  conversations    jsonb not null default '[]'::jsonb,   -- array of {"thread_id", "entry"} email log
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

-- updated_at maintenance
create or replace function set_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

drop trigger if exists customers_set_updated_at on customers;
create trigger customers_set_updated_at
  before update on customers
  for each row execute function set_updated_at();

-- Lock everything down: RLS on with no policies means only the service key
-- (which bypasses RLS) can touch the table. Jason is the sole client.
alter table customers enable row level security;

-- Expose the custom schema/table to Supabase's Data API roles. RLS still
-- protects table access; service_role/secret keys bypass RLS for Jason.
grant usage on schema jason_memory to anon, authenticated, service_role;
grant all on all tables in schema jason_memory to anon, authenticated, service_role;
grant all on all sequences in schema jason_memory to anon, authenticated, service_role;
grant execute on all functions in schema jason_memory to anon, authenticated, service_role;

alter default privileges in schema jason_memory
  grant all on tables to anon, authenticated, service_role;
alter default privileges in schema jason_memory
  grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema jason_memory
  grant execute on functions to anon, authenticated, service_role;

notify pgrst, 'reload schema';
