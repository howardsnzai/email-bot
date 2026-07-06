-- Jason memory system — Supabase schema
-- Mirrors jason/storage.py's Store interface:
--   structured fields -> columns/jsonb, freeform notes -> text, media -> buckets (Cloudflare R2).
-- Run in the Supabase SQL editor (or via the Supabase MCP connector).

create table if not exists agents (
  email       text primary key,
  details     jsonb not null default '{}'::jsonb,  -- name, phone, agency pointer, defaults...
  profile     text  not null default '',           -- freeform durable preferences (profile.md)
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table if not exists agencies (
  domain      text primary key,                    -- shared branding via the agent's agency pointer
  notes       text not null default '',            -- brand colours, standard outro, office details
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table if not exists jobs (
  thread_id       text primary key,                -- one job per Gmail thread
  job_id          text not null,
  agent_email     text not null references agents(email),
  status          text not null default 'intake',
  paid            boolean not null default false,  -- code-owned: set only by the Stripe webhook path
  last_inbound_at timestamptz,
  data            jsonb not null default '{}'::jsonb,  -- full job dict (intake, photos, amenities...)
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists jobs_agent_email_idx on jobs (agent_email);
create index if not exists jobs_status_idx on jobs (status);

create table if not exists video_jobs (
  agent_email text not null references agents(email),
  job_id      text not null,
  record      jsonb not null default '{}'::jsonb,  -- per-job history: style pack, video link, changes
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  primary key (agent_email, job_id)
);

create table if not exists emails (
  id          bigint generated always as identity primary key,
  agent_email text not null,
  thread_id   text not null,
  entry       jsonb not null,                      -- {direction, from, subject, body, at, ...}
  created_at  timestamptz not null default now()
);
create index if not exists emails_agent_thread_idx on emails (agent_email, thread_id, id);

-- updated_at maintenance
create or replace function set_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

do $$
declare t text;
begin
  foreach t in array array['agents', 'agencies', 'jobs', 'video_jobs'] loop
    execute format('drop trigger if exists %I on %I', t || '_updated_at', t);
    execute format(
      'create trigger %I before update on %I for each row execute function set_updated_at()',
      t || '_updated_at', t);
  end loop;
end $$;

-- Lock everything down: RLS on with no policies means only the service key
-- (which bypasses RLS) can touch these tables. Jason is the sole client.
alter table agents     enable row level security;
alter table agencies   enable row level security;
alter table jobs       enable row level security;
alter table video_jobs enable row level security;
alter table emails     enable row level security;
