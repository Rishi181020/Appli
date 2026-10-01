-- Activity heatmap + streaks: when each application was submitted.
-- Run once in the Supabase SQL editor, after 006 (safe to re-run).

-- submitted_at feeds the heatmap and streaks (older submissions are dated by their last update);
-- status_changed_at records when a job last changed status.
alter table jobs add column if not exists submitted_at timestamptz;
alter table jobs add column if not exists status_changed_at timestamptz;
update jobs set submitted_at = updated_at where status = 'submitted' and submitted_at is null;
update jobs set status_changed_at = updated_at where status_changed_at is null;
create index if not exists jobs_submitted_at_idx on jobs (owner_id, submitted_at) where submitted_at is not null;

create or replace function jobs_status_times() returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' or new.status is distinct from old.status then
    new.status_changed_at = now();
    if new.status = 'submitted' and new.submitted_at is null then
      new.submitted_at = now();
    end if;
  end if;
  return new;
end $$;
drop trigger if exists jobs_status_times on jobs;
create trigger jobs_status_times before insert or update on jobs
  for each row execute function jobs_status_times();
