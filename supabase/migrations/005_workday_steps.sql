-- Workday page by page: a job waits in 'submission_required' while the person clicks Save and Continue on the
-- current Workday page. Run once in the Supabase SQL editor, after 004 (safe to re-run).
alter table jobs drop constraint if exists jobs_status_check;
alter table jobs add constraint jobs_status_check check (status in
  ('queued','filling','tailoring','submission_required','ready_for_review','submitted','skipped','needs_manual','failed'));
