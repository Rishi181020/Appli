-- 'needs_help': a Workday page has questions Appli couldn't answer; they're listed on the dashboard, and answers saved
-- there are typed into the open page. Run once in the Supabase SQL editor, after 005 (safe to re-run).
alter table jobs drop constraint if exists jobs_status_check;
alter table jobs add constraint jobs_status_check check (status in
  ('queued','filling','tailoring','needs_help','submission_required','ready_for_review','submitted','skipped',
   'needs_manual','failed'));
