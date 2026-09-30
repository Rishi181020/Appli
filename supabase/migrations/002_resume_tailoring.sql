-- Resume tailoring: run once in the Supabase SQL editor (safe to re-run).
alter table jobs add column if not exists resume_match  jsonb;  -- posting terms, score per base resume, chosen resume
alter table jobs add column if not exists resume_edits  jsonb;  -- proposed edits, gaps, problems, projected match
alter table jobs add column if not exists resume_review text
  check (resume_review in ('pending', 'built', 'dismissed'));
alter table jobs add column if not exists resume_file   text;   -- path of the built tailored PDF
create index if not exists jobs_resume_review_idx on jobs (resume_review) where resume_review is not null;
