-- Multi-user: every row belongs to one person, and each person can only see and change their own rows.
-- Run once in the Supabase SQL editor, AFTER 002 and 003. Safe to re-run.
-- Existing rows are given to the project owner below: change the email if yours differs.

do $$
declare owner uuid;
begin
  select id into owner from auth.users where email = 'rdixit@scu.edu';
  if owner is null then
    raise exception 'Owner account not found in auth.users - edit the email at the top of this file';
  end if;

  -- 1) owner_id on every table (defaults to whoever is signed in when a row is inserted)
  alter table jobs    add column if not exists owner_id uuid references auth.users(id) on delete cascade;
  alter table answers add column if not exists owner_id uuid references auth.users(id) on delete cascade;
  alter table runs    add column if not exists owner_id uuid references auth.users(id) on delete cascade;
  update jobs    set owner_id = owner where owner_id is null;
  update answers set owner_id = owner where owner_id is null;
  update runs    set owner_id = owner where owner_id is null;
end $$;

alter table jobs    alter column owner_id set default auth.uid(), alter column owner_id set not null;
alter table answers alter column owner_id set default auth.uid(), alter column owner_id set not null;
alter table runs    alter column owner_id set default auth.uid(), alter column owner_id set not null;

-- 2) uniqueness is per person: two people can list the same posting or save the same question
alter table jobs    drop constraint if exists jobs_dedupe_key_key;
alter table answers drop constraint if exists answers_question_key_key;
create unique index if not exists jobs_owner_dedupe_uq     on jobs (owner_id, dedupe_key);
create unique index if not exists answers_owner_question_uq on answers (owner_id, question_key);
create index if not exists jobs_owner_status_idx on jobs (owner_id, status);

-- 3) the profile that every model call uses (written by the dashboard's Profile onboarding)
create table if not exists profiles (
  owner_id   uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  data       jsonb not null default '{}'::jsonb,   -- structured fields (source of truth)
  markdown   text not null default '',             -- rendered profile.md (what the models read)
  updated_at timestamptz not null default now()
);
drop trigger if exists profiles_touch on profiles;
create trigger profiles_touch before update on profiles for each row execute function touch_updated_at();

-- 4) row-level security: owner only (replaces the single-email policies)
alter table profiles enable row level security;
drop policy if exists owner_jobs on jobs;
drop policy if exists owner_answers on answers;
drop policy if exists owner_runs on runs;
drop policy if exists own_jobs on jobs;
drop policy if exists own_answers on answers;
drop policy if exists own_runs on runs;
drop policy if exists own_profile on profiles;
create policy own_jobs    on jobs     for all to authenticated using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy own_answers on answers  for all to authenticated using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy own_runs    on runs     for all to authenticated using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy own_profile on profiles for all to authenticated using (owner_id = auth.uid()) with check (owner_id = auth.uid());

-- 5) screenshots are no longer taken
drop policy if exists owner_screenshots on storage.objects;
