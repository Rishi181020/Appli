import { useCallback, useEffect, useMemo, useState } from "react";
import { BuildResult, ResumeEdit, resumeLabel, ResumeJob, agent, matchTone, pctText, supabase, tailoredPdfUrl, timeAgo } from "./lib";
import { Avatar } from "./JobsView";
import { IconAlert, IconCheck, IconExternal, IconRefresh } from "./Icons";

const COLS = "id,company,role,ats,status,updated_at,resume_match,resume_edits,resume_review,resume_file";
const pct = (n: number | null | undefined) => (n == null ? "–" : `${Math.round(n * 100)}%`);

/** Each Skills line that gains something, with the ticked skills highlighted where they'll appear. */
function SkillsPreview({ edits, accepted }: { edits: ResumeEdit[]; accepted: Set<string> }) {
  const lines = new Map<string, { label: string; before: string; added: string[] }>();
  edits.forEach((e) => {
    if (!lines.has(e.unit_id)) lines.set(e.unit_id, { label: e.context, before: e.before, added: [] });
    if (accepted.has(e.id)) lines.get(e.unit_id)!.added.push(e.skill ?? e.keywords_added[0]);
  });
  const shown = [...lines.values()].filter((l) => l.added.length);
  if (!shown.length) return <p className="muted small">Tick a skill to see where it goes.</p>;
  return (
    <div className="skills-preview">
      {shown.map((l) => (
        <div key={l.label} className="sp-line">
          <b>{l.label}:</b> {l.before}
          {l.added.map((s) => (
            <span key={s}>
              , <mark>{s}</mark>
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

function Card({ job, onChanged }: { job: ResumeJob; onChanged: () => void }) {
  const edits = job.resume_edits;
  const match = job.resume_match;
  // skills backed by your materials start ticked; ones found nowhere start unticked ("add only if true")
  const [accepted, setAccepted] = useState<Set<string>>(
    new Set((edits?.edits ?? []).filter((e) => e.default ?? true).map((e) => e.id))
  );
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BuildResult | null>(null);
  const built = job.resume_review === "built";

  const chosen = useMemo(() => (edits?.edits ?? []).filter((e) => accepted.has(e.id)), [edits, accepted]);
  const addedWords = chosen.flatMap((e) => e.added_words);
  const removedWords = chosen.flatMap((e) => e.removed_words);

  const toggle = (id: string) =>
    setAccepted((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  async function build() {
    setBusy(true);
    setResult(null);
    try {
      const r = await agent<BuildResult>("/api/resume/build", { job_id: job.id, accepted: [...accepted] });
      setResult(r);
      if (r.ok) onChanged();
    } catch (e) {
      setResult({ ok: false, error: (e as Error).message });
    }
    setBusy(false);
  }

  const [fillMsg, setFillMsg] = useState<{ ok: boolean; text: string } | null>(null);
  async function fillNow() {
    // a run of just this job, with the tailored resume (a run already going picks it up first on its next pick instead)
    setBusy(true);
    setFillMsg(null);
    try {
      await agent("/api/run", { ids: [job.id] });
      setFillMsg({ ok: true, text: "Filling it now with your tailored resume: a browser window is opening." });
    } catch (e) {
      setFillMsg({ ok: false, text: `${(e as Error).message} It stays first in the queue.` });
    }
    setBusy(false);
  }

  async function dismiss() {
    // the job leaves the Tailoring state and is filled with the best base resume on the next run
    try {
      await agent("/api/resume/dismiss", { job_id: job.id });
    } catch {
      await supabase.from("jobs").update({ resume_review: "dismissed" }).eq("id", job.id);
    }
    onChanged();
  }

  if (!edits || !match) return null;
  const rank = Object.entries(match.scores).sort((a, b) => b[1].pct - a[1].pct);

  return (
    <article className={`rcard ${built ? "built" : ""}`}>
      <header className="rhead">
        <Avatar name={job.company} size={40} />
        <div className="rwho">
          <div className="company">{job.company}</div>
          <div className="role">{job.role}</div>
        </div>
        <div className="rpct">
          <span className="from">{pct(edits.pct_before)}</span>
          <span className="arrow">→</span>
          <span className={edits.pct_after >= edits.threshold ? "to good" : "to"}>{pct(built ? edits.built?.pct_after ?? edits.pct_after : edits.pct_after)}</span>
          <small>match</small>
        </div>
      </header>

      <div className="scores">
        {rank.map(([k, s]) => (
          <span key={k} className={`score ${k === edits.base ? "on" : ""}`} title={`Missing: ${s.missing.join(", ") || "nothing"}`}>
            {resumeLabel(k, match.labels)} {pct(s.pct)}
            {k === edits.base && <em>used</em>}
          </span>
        ))}
        <span className="muted small">Target {pct(edits.threshold)} · job is {job.ats}, status {job.status.replace(/_/g, " ")} · {timeAgo(job.updated_at)}</span>
      </div>

      {built ? (
        <div className="banner ok-b">
          <IconCheck /> Built with {edits.built?.accepted.length ?? 0} added skill(s) and saved as <code>{job.resume_file}</code>. Skills added:{" "}
          <b>{(edits.built?.added_words ?? []).join(", ") || "none"}</b>.
          <a className="btn small-btn" href={tailoredPdfUrl(job.id)} target="_blank" rel="noreferrer">
            Open PDF <IconExternal />
          </a>
        </div>
      ) : null}

      <section className="rsec">
        <h3>
          Missing skills <span className="count-pill">{chosen.length} of {edits.edits.length} ticked</span>
        </h3>
        <p className="muted small" style={{ marginBottom: 8 }}>
          Only your Technical Skills section changes. Each skill is added in the posting’s exact wording to the line shown.
          {edits.pct_if_all != null && ` Match with the ticked-by-default skills: ${pct(edits.pct_after)}; with every skill: ${pct(edits.pct_if_all)}.`}
        </p>
        {edits.edits.map((e: ResumeEdit) => (
          <label key={e.id} className={`skillrow ${accepted.has(e.id) ? "on" : ""} ${e.support ?? "backed"}`}>
            <input type="checkbox" checked={accepted.has(e.id)} onChange={() => toggle(e.id)} />
            <div className="skill-main">
              <div className="skill-name">
                <b>{e.skill ?? e.keywords_added[0]}</b>
                {e.required && <span className="req-tag">required</span>}
                {e.category && <span className="cat-tag">{e.category}</span>}
                <span className="muted small">→ {e.context}</span>
              </div>
              {e.support === "not_found" ? (
                <div className="small warn-text">Not in any of your resumes or your profile. Tick only if you really have this skill.</div>
              ) : e.support === "shown_as" ? (
                <div className="small muted">Your resumes show this under another name, so it’s honest to list it in the posting’s words.</div>
              ) : e.evidence ? (
                <div className="small muted evidence">In your materials: “{e.evidence}”</div>
              ) : null}
            </div>
          </label>
        ))}
      </section>

      <section className="rsec">
        <h3>Your Skills section with the ticked skills</h3>
        <SkillsPreview edits={edits.edits} accepted={accepted} />
      </section>

      <section className="rsec">
        <h3>Words added</h3>
        <div className="chips-row">
          {addedWords.length ? addedWords.map((w, i) => <span key={i} className="w add">{w}</span>) : <span className="muted small">none</span>}
          {removedWords.length > 0 && removedWords.map((w, i) => <span key={`r${i}`} className="w del">{w}</span>)}
        </div>
      </section>

      {edits.problems.length > 0 && (
        <section className="rsec">
          <h3>What went wrong</h3>
          <ul className="problems">
            {edits.problems.map((p, i) => (
              <li key={i}>
                <IconAlert />
                <div>
                  {p.kind === "rejected_edit" && (
                    <>
                      <b>An edit was rejected{p.context ? ` (${p.context})` : ""}:</b> {(p.reasons ?? []).join("; ")}
                    </>
                  )}
                  {p.kind === "not_added" && (
                    <>
                      <b>Supported but not worked in:</b> {(p.terms ?? []).join(", ")}. {p.detail}
                    </>
                  )}
                  {!["rejected_edit", "not_added"].includes(p.kind) && <>{p.detail}</>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {result && (
        <div className={`banner ${result.ok ? "ok-b" : "error"}`}>
          {result.ok ? (
            <>
              Saved <code>{result.file}</code>, match {pct(edits.pct_before)} → {pct(result.pct_after)}.{" "}
              {result.next ?? ""}
              {result.db_warning && <div className="small">Not recorded in the database: run the 002 migration. ({result.db_warning})</div>}
              <a className="btn small-btn" href={tailoredPdfUrl(job.id)} target="_blank" rel="noreferrer">
                Open PDF <IconExternal />
              </a>
              {result.queued && (
                <button className="btn primary small-btn" disabled={busy} onClick={fillNow}>
                  Fill it now
                </button>
              )}
            </>
          ) : (
            result.error
          )}
        </div>
      )}
      {fillMsg && <div className={`banner ${fillMsg.ok ? "ok-b" : "error"}`}>{fillMsg.text}</div>}

      <footer className="ractions">
        <button className="btn primary" disabled={busy || accepted.size === 0} onClick={build}>
          {busy ? "Building…" : built ? `Rebuild with ${accepted.size} skill(s)` : `Build tailored resume (${accepted.size} skill${accepted.size === 1 ? "" : "s"})`}
        </button>
        {!built && (
          <button className="btn ghost" onClick={dismiss} disabled={busy} title="Fill this job with the best base resume instead">
            Use base resume instead
          </button>
        )}
      </footer>
    </article>
  );
}

export default function ResumeReview() {
  const [jobs, setJobs] = useState<ResumeJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showBuilt, setShowBuilt] = useState(false);

  const load = useCallback(async () => {
    const { data, error } = await supabase
      .from("jobs")
      .select(COLS)
      .or("resume_review.in.(pending,built),status.eq.tailoring")
      .order("updated_at", { ascending: false });
    if (error) setError(error.message);
    else {
      setError(null);
      setJobs(data as ResumeJob[]);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  const pending = jobs.filter((j) => j.resume_review === "pending");
  const drafting = jobs.filter((j) => j.status === "tailoring" && j.resume_review !== "pending");
  const built = jobs.filter((j) => j.resume_review === "built");
  const migration = error && /resume_(review|edits|match)|column/i.test(error);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Resume review</h1>
          <p className="muted">
            When your best resume matches a job under 80%, a tailored version is drafted here. Every change is checked against your own
            resumes and profile: nothing you don’t have is ever added.
          </p>
        </div>
        <button className="btn ghost icon-btn" onClick={load} title="Refresh">
          <IconRefresh />
        </button>
      </header>

      {migration ? (
        <div className="banner error">
          The resume columns aren’t in your database yet. Run <code>supabase/migrations/002_resume_tailoring.sql</code> once in the Supabase
          SQL editor, then refresh.
        </div>
      ) : (
        error && <div className="banner error">Couldn’t load: {error}</div>
      )}

      {drafting.length > 0 && (
        <div className="list">
          {drafting.map((j) => (
            <div className="drafting" key={j.id}>
              <span className="spinner" />
              <b>{j.company}</b>
              <span className="muted">{j.role}</span>
              <span className={`matchpill ${matchTone(j.resume_match?.pct)}`}>{pctText(j.resume_match?.pct)}</span>
              <span className="muted small">Drafting a tailored resume… the job is paused and won’t be filled until you build it.</span>
            </div>
          ))}
        </div>
      )}

      {loading ? (
        <div className="skeleton row-skel" />
      ) : pending.length === 0 ? (
        <div className="empty">
          <div className="empty-art">✦</div>
          <b>Nothing to review</b>
          <span className="muted">Suggestions appear here after a run, for jobs where your best resume matched under 80%.</span>
        </div>
      ) : (
        pending.map((j) => <Card key={j.id} job={j} onChanged={load} />)
      )}

      {built.length > 0 && (
        <>
          <button className="btn ghost" onClick={() => setShowBuilt((v) => !v)}>
            {showBuilt ? "Hide" : "Show"} built resumes ({built.length})
          </button>
          {showBuilt && built.map((j) => <Card key={j.id} job={j} onChanged={load} />)}
        </>
      )}
    </div>
  );
}
