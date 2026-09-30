-- Run once in the Supabase SQL editor.
create table if not exists jobs (
  id bigint generated always as identity primary key,
  dedupe_key text not null unique,
  company text not null,
  role text,
  location text,
  salary text,
  url text,
  source text,
  fit_tier int,
  days_posted int,
  ats text,
  status text not null default 'queued'
    check (status in ('queued','filling','ready_for_review','submitted','skipped','needs_manual','failed')),
  status_reason text,
  resume_used text,
  cover_letter text,
  screenshot_path text,
  filled_fields jsonb,
  unanswered jsonb,
  attempts int not null default 0,
  run_id bigint,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists jobs_queue_idx on jobs (status, fit_tier, days_posted);

create table if not exists answers (
  id bigint generated always as identity primary key,
  question_key text not null unique,
  question_text text,
  answer text not null,
  updated_at timestamptz not null default now()
);

create table if not exists runs (
  id bigint generated always as identity primary key,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  counts jsonb
);

create or replace function touch_updated_at() returns trigger as $$
begin new.updated_at = now(); return new; end; $$ language plpgsql;
drop trigger if exists jobs_touch on jobs;
create trigger jobs_touch before update on jobs for each row execute function touch_updated_at();
drop trigger if exists answers_touch on answers;
create trigger answers_touch before update on answers for each row execute function touch_updated_at();

-- Row Level Security: only the owner's login can read/write. The runner's service-role key bypasses RLS.
alter table jobs enable row level security;
alter table answers enable row level security;
alter table runs enable row level security;
create policy owner_jobs on jobs for all to authenticated
  using ((auth.jwt() ->> 'email') = 'rdixit@scu.edu') with check ((auth.jwt() ->> 'email') = 'rdixit@scu.edu');
create policy owner_answers on answers for all to authenticated
  using ((auth.jwt() ->> 'email') = 'rdixit@scu.edu') with check ((auth.jwt() ->> 'email') = 'rdixit@scu.edu');
create policy owner_runs on runs for all to authenticated
  using ((auth.jwt() ->> 'email') = 'rdixit@scu.edu') with check ((auth.jwt() ->> 'email') = 'rdixit@scu.edu');

-- Private bucket for screenshots.
insert into storage.buckets (id, name, public) values ('screenshots', 'screenshots', false)
  on conflict (id) do nothing;
create policy owner_screenshots on storage.objects for select to authenticated
  using (bucket_id = 'screenshots' and (auth.jwt() ->> 'email') = 'rdixit@scu.edu');
