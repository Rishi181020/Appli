# Jobright parity plan

What jobright.ai does for job matching, search and AI resume tailoring, worked out from its shipped web bundle and public
endpoints (REA captures, 2026-10-13), and how to give Appli each feature locally. Appli already covers a lot of it; each
item names what exists, what to change, and where.

## What jobright does (findings)

### Job search and ranking

- Backend `swan-api.jobright.ai`. Feed: `GET /swan/recommend/list/jobs?sortCondition=RELEVANCE|FRESHNESS|MATCH_SCORE&count=10&refresh&syncRerank`.
  Also `similar/jobs`, `count-filter-jobs`, `count-hidden-jobs`, `job/like|ignore|apply|report`, `filter/job-alert`, `filter/digest`.
- Retrieval then rerank. The public landing feed exposes the notes: `retriever: "titleTaxonomy"`, `taxonomy_ids: "01-01-01"`
  (title -> taxonomy code), and `rankScore = retrieve_score x freshness_weight x mix_rank_weight` (e.g. 1.0 x 2.28 x 1.5 = 3.42).
  Freshness is a multiplier that decays with age.
- Every posting is pre-parsed into structured fields: `qualifications.mustHave / preferredHave`, split by
  `yoe / education / hardSkill / softSkill`, plus `jobSeniority`, `minYearsOfExperience`, `workModel`, `jobTaxonomyV3`,
  `coreResponsibilities`, `isH1bSponsor`, `isCitizenOnly`, `isClearanceRequired`, `applicantsCount`.
- Jobs come mostly from LinkedIn (`linkedin_job_id` on each), with company data (size, stage, funding, H-1B history).

### Match score

- Per user, computed server side, four parts: `q_job_skill_match`, `q_seniority_match`, `q_industry_match`,
  `education_match_score`, plus a per-skill list (`skillMatchingScores`) over the job's hard and soft skills.
- Combined into `displayScore` (0-100). UI bands: `<=70 less-match`, `71-85 match`, `>85 strong-match`. The card shows
  the ring, the band label, and the top 3 reasons. The weighting of the four parts is not visible from outside.

### AI resume tailor (`/jobs/resume/edit/[id]`)

- Flow from the API and UI events: `prep` -> optional skill pick (`tailor_multi_skill_selection`: the user ticks which
  missing skills to target) -> generate (`overview`, `diff` / `new-diff`) -> review -> `apply` / `save` -> export PDF/DOCX.
- `keywords-classification` sorts the posting's keywords against the resume; `expect-score` predicts the match score
  after tailoring (the before/after "score card").
- Edits are path-addressed: `workExperience{i}.jobDescriptions{j}`, `workExperience{i}.summary`, `projects{i}.contents{j}`.
  Views: `panel` and side-by-side `compare`; in-place editing of any line; `history` of tailored versions.
- Chat refine: `POST /swan/resume-tailor/v2/chat`, streamed (text/event-stream), cancellable. Modes `tailor | rollback | rewrite`.
  Chat card types: `text, score_card, regenerate, regenerate_success, reverted, keyword_verify, keyword_matching, feedback`.
  Per-change `regenerations/{id}/actions` (regenerate or revert), `custom-prompt`, voice input via `transcribe`.
- Keyword verify (the honesty check): for a keyword it added, it asks "Did you use X?" with
  **Yes, keep it / No, remove it / I used something similar** (the last one pre-fills the chat with
  "Here is what I actually did with X:").
- Resume rewriter / diagnose (`resume-rewriter/diagnose/*`): per-section issues (e.g. `MISSING_SUMMARY`, `MISSING_SKILLS`)
  with fixes. Export styles: template (standard/compact), header alignment, date format, skills layout and columns, divider.

## What Appli already has

- Search: `runner/jobsearch.py` (Greenhouse/Lever/Ashby/Workday boards, SimplifyJobs lists, remote boards; prefs;
  filter; `rank()`; screening; resume match % via `resume/select.evaluate`). UI `dashboard/src/FindJobs.tsx`.
- Matching: `runner/resume/match.py` (LLM extracts terms, code scores coverage: exact = full, synonym = half, required x2).
- Tailoring: `resume/tailor.py` (skills-line suggestions, unsupported ones unticked "add only if true"),
  `resume/bankbuild.py` (plan -> write -> critique -> verify quotes and numbers -> render), Resume review approval,
  `ResumeStudio.tsx` (paste a posting, analyze, accept edits, build).

Appli's tailoring is already stricter on honesty than jobright's (every bullet quotes the bank). The gaps are mostly
the interactive layer and the match breakdown.

## Plan

### Phase 1: match score like jobright (small, high value)

1. **Structured qualifications per posting.** Extend `match.extract_terms` (same LLM call) to also return
   `seniority`, `min_yoe`, `education`, `industry`, and tag each term `hard | soft | tool | cert` and `must | preferred`.
   Cache per posting key. Files: `runner/resume/match.py`.
2. **Four-part score.** New `breakdown(profile, resume_text, terms)` in `match.py`: skills = current coverage;
   seniority = profile YOE vs `min_yoe` and level; education = degree vs requirement; industry = profile industries vs
   company category. Overall = weighted mean (start 0.5 / 0.2 / 0.15 / 0.15; jobright's weights are unknown, tune later).
   Store on `Found.match` and in `found.json`. Test: one fixture posting + profile, assert each part and the total.
3. **Bands and reasons in the UI.** `FindJobs.tsx`: ring with `<=70 / 71-85 / >85` colors, the band label, top 3
   reasons ("Missing: Kubernetes", "3 yrs required, you have 1"). Reuse `matchTone` / `pctText` from `lib.ts`.
4. **Freshness as a multiplier.** `jobsearch.rank()`: `rank = relevance x freshness(age) x boost`, with `freshness`
   decaying from about 2.3 (today) toward 1. Keeps a perfect old match from outranking everything new.
5. **Sort options.** Relevance (rank) / Most recent / Top matched on the Find jobs list (client side, data is already there).

### Phase 2: search features

6. **Feedback loop.** Like / Not interested / Applied on each result, kept in `users/<Name>/found.json`. Ignored
   companies and titles get down-weighted in `rank()`; "Not interested" hides the job. Show a hidden-jobs count.
7. **More filters.** Work model (onsite/hybrid/remote), seniority, sponsorship (H-1B), citizenship and clearance, company
   size, and an exclude-companies list. Most fields come from step 1 and the existing screens. Files: `Prefs`, `keep()`.
8. **Title taxonomy retrieval.** Small mapping of title families (SWE, data, ML, PM, ...) in `jobsearch.py` so
   "Software Engineer" also retrieves "Software Developer", "Backend Engineer" and similar, as jobright's `titleTaxonomy` does.
   Replaces strict `title_matches` when a family matches.
9. **Similar jobs.** For a liked job, rank other results by term overlap (Jaccard over extracted hard skills). No new model.
10. **Daily digest (optional).** Scheduled search (existing `start.bat` / Task Scheduler) that writes new strong matches
    to the dashboard Activity feed.
11. **H-1B history (optional).** Public DOL LCA disclosure files give per-company sponsorship counts; load once into
    `users/` cache and show "Past sponsorship" as jobright does.

### Phase 3: interactive tailor (ResumeStudio becomes the tailor editor)

12. **Pick skills before generating.** After analyze, show the missing keywords grouped by category (have it / similar /
    don't have it) and let the person tick which to target; feed only those into `bankbuild` / `tailor`. Mirrors
    `keywords-classification` and multi-skill selection.
13. **Before/after score card.** Compute `breakdown()` on the base resume and on the draft; show both
    (jobright's `expect-score`). No new model; code only.
14. **Compare view and path-addressed edits.** Represent the draft as edits keyed by `section[i].bullet[j]`, show base vs
    tailored side by side with a word diff (`runner/resume` already has `word_diff`), allow in-place edits, and accept or
    revert each change.
15. **Keyword verify cards.** For every term the writer added that `match.supported()` can't find in the bank or
    profile, ask "Did you use X?" with Keep / Remove / "I used something similar" (opens the chat prefilled). Answers
    go into the experience bank so the next build doesn't ask again.
16. **Chat refine with streaming.** `POST /api/resume-studio/chat` on `runner/server.py`, streamed with server-sent
    events, cancellable. It edits one path at a time ("make bullet 2 shorter", "focus on the Kafka work"), with
    regenerate and revert per change and a custom standing prompt. Every chat edit goes through the same `verify` step as
    `bankbuild` (quotes and numbers must come from the bank), so honesty holds.
17. **Version history.** Keep each built tailored resume per job under `resumes/tailored/<job id>/`, listed in the
    studio with restore.
18. **Export styles.** Standard/compact template, header alignment, date format, skills columns, divider, applied in
    `resume/tex.py` and `resume/word.py`. One style per person in their config.
19. **Resume diagnose.** Section checks run in code first (missing summary or skills, bullets without numbers, weak verbs,
    over-long bullets, a term used more than 3 times), the LLM only for wording fixes. Shows issues with one-click fixes
    that open as tailor edits.
