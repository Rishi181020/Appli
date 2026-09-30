-- Jev + tailoring state. Run once in the Supabase SQL editor, AFTER 002_resume_tailoring.sql (safe to re-run).
alter table jobs drop constraint if exists jobs_status_check;
alter table jobs add constraint jobs_status_check check (status in
  ('queued','filling','tailoring','ready_for_review','submitted','skipped','needs_manual','failed'));
alter table jobs add column if not exists match_pct numeric;
update jobs set match_pct = (resume_match->>'pct')::numeric where match_pct is null and resume_match ? 'pct';

alter table answers add column if not exists source text not null default 'manual'
  check (source in ('manual', 'learned'));
alter table answers add column if not exists options jsonb;
alter table answers add column if not exists uses int not null default 0;
