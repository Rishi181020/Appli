import { useEffect, useState } from "react";
import { agent, matchTone, pctText, resumeLabel, skipExplanation } from "./lib";
import { IconDoc } from "./Icons";
import StudioDraft, { Keyword, ScoreCard, StudioView } from "./StudioDraft";

type History = {
  drafts: { draft_id: string; company: string; role: string; mode: string | null; pct: number | null; versions: number }[];
  jobs: { job_id: number; company: string; role: string; current: string[]; versions: { id: string; at: string; files: string[] }[] }[];
};

const GROUPS: [Keyword["group"], string, string][] = [
  ["have", "You have these", "Your bank, profile or another resume shows them: safe to add in the posting's wording."],
  ["similar", "You've done something similar", "Shown under another name: the posting's wording can be used for it."],
  ["missing", "Not in your materials", "Tick one only if it's true. You'll be asked to confirm it."],
];

// this browser remembers the open draft and what you typed, so a reload never loses your place (the draft itself,
// with its edits and chat, is saved on this computer by the server)
const SAVED = "appli.studio";
const remembered = (): { company?: string; role?: string; description?: string; draft_id?: string } => {
  try {
    return JSON.parse(localStorage.getItem(SAVED) || "{}");
  } catch {
    return {};
  }
};

export default function ResumeStudio() {
  const [company, setCompany] = useState(() => remembered().company ?? "");
  const [role, setRole] = useState(() => remembered().role ?? "");
  const [description, setDescription] = useState(() => remembered().description ?? "");
  const [view, setView] = useState<StudioView | null>(null);
  const [targets, setTargets] = useState<string[]>([]);
  const [extractionConfirmed, setExtractionConfirmed] = useState(false);
  const [busy, setBusy] = useState<"analyze" | "generate" | "diagnose" | "open" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<History | null>(null);

  const loadHistory = () => agent<History>("/api/resume-studio/drafts", {}).then(setHistory).catch(() => setHistory(null));
  useEffect(() => {
    loadHistory();
    const id = remembered().draft_id;
    if (id) run("open", "/api/resume-studio/open", { draft_id: id });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(SAVED, JSON.stringify({ company, role, description, draft_id: view?.draft_id }));
    } catch {
      /* private window: nothing remembered */
    }
  }, [company, role, description, view?.draft_id]);
  const show = (v: StudioView) => {
    setView(v);
    setTargets(v.targets ?? []);
  };
  const reset = () => { setView(null); setError(null); setExtractionConfirmed(false); };

  async function run(name: NonNullable<typeof busy>, path: string, body: object) {
    setBusy(name);
    setError(null);
    try {
      show(await agent<StudioView>(path, body));
      if (name !== "diagnose") loadHistory();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  const analyze = () => { reset(); return run("analyze", "/api/resume-studio/analyze", { company, role, description }); };
  const generate = () => view && run("generate", "/api/resume-studio/generate", { draft_id: view.draft_id, targets, extraction_confirmed: extractionConfirmed });

  async function restoreJob(job_id: number, version: string) {
    try {
      const r = await agent<{ jobs: History["jobs"] }>("/api/resume-studio/restore-job", { job_id, version });
      setHistory((h) => (h ? { ...h, jobs: r.jobs } : h));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const pdfRebuild = view?.edits.format === "pdf_rebuild" && !view.bank_ready;
  const canGenerate = view && (view.bank_ready || view.match.tailorable || view.edits.format === "pdf_rebuild");
  const toggle = (term: string) => setTargets((t) => (t.includes(term) ? t.filter((x) => x !== term) : [...t, term]));

  return (
    <div className="page resume-studio">
      <header className="page-head">
        <div>
          <h1>Tailor resume</h1>
          <p className="muted">Paste a job description: see how your resume matches, pick the skills to target, and get a resume rewritten from your experience bank that you can compare, edit and refine.</p>
        </div>
      </header>
      <section className="panel studio-input">
        <div className="studio-fields">
          <label className="pf-field">Company (optional)<input maxLength={200} disabled={!!busy} value={company} onChange={(e) => { setCompany(e.target.value); reset(); }} placeholder="Company name" /></label>
          <label className="pf-field">Job title (optional)<input maxLength={200} disabled={!!busy} value={role} onChange={(e) => { setRole(e.target.value); reset(); }} placeholder="Software Engineer" /></label>
        </div>
        <label className="pf-field">Job description<textarea rows={12} maxLength={40000} disabled={!!busy} value={description} onChange={(e) => { setDescription(e.target.value); reset(); }} placeholder="Paste the full description, including responsibilities, required skills and qualifications…" /><small>{description.trim().length.toLocaleString()} characters · At least 300 characters</small></label>
        <button className="btn primary" disabled={!!busy || description.trim().length < 300} onClick={analyze}><IconDoc />{busy === "analyze" ? "Matching your resumes…" : "Analyze"}</button>
      </section>
      {busy && busy !== "diagnose" && <div className="banner" role="status"><span className="spinner" /> {busy === "analyze" ? "Checking eligibility and scoring your resumes. This can take a minute." : busy === "generate" ? "Claude is writing your tailored resume from your experience bank. This takes a minute or two." : "Opening…"}</div>}
      {error && <div className="banner error" role="alert">{error}</div>}

      {history && (history.drafts.length > 0 || history.jobs.length > 0) && (
        <section className="panel studio-result">
         <details open={!view}>
          <summary><h2 className="inline">Earlier resumes ({history.drafts.length})</h2></summary>
          {history.drafts.length > 0 && (
            <ul className="studio-versions">
              {history.drafts.map((d) => (
                <li key={d.draft_id}>
                  <span><b>{d.company}</b> · {d.role}</span>
                  <span className="small muted">{d.mode === "bank" ? "rewritten from your bank" : d.mode === "skills" ? "skills added" : "not generated"}{d.pct != null ? ` · ${pctText(d.pct)}` : ""}{d.versions ? ` · ${d.versions} version${d.versions === 1 ? "" : "s"}` : ""}</span>
                  {view?.draft_id === d.draft_id ? <span className="chip-tag ok-tag">Open now</span> :
                    <button className="btn ghost small-btn" disabled={!!busy} onClick={() => run("open", "/api/resume-studio/open", { draft_id: d.draft_id })}>Open</button>}
                </li>
              ))}
            </ul>
          )}
          {history.jobs.length > 0 && (
            <details>
              <summary className="small">Earlier versions of your applications' tailored resumes</summary>
              {history.jobs.map((j) => (
                <div key={j.job_id} className="studio-entry">
                  <p className="small"><b>{j.company}</b> · {j.role} <span className="muted">now: {j.current.join(", ") || "none"}</span></p>
                  <ul className="studio-versions">
                    {j.versions.map((v) => (
                      <li key={v.id}>
                        <span>{v.at}</span>
                        <span className="small muted">{v.files.filter((f) => /\.(pdf|docx)$/.test(f)).join(", ")}</span>
                        <button className="btn ghost small-btn" onClick={() => restoreJob(j.job_id, v.id)}>Restore</button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </details>
          )}
         </details>
        </section>
      )}

      {view && (
        <>
          {view.restriction && <div className="banner warn"><b>Eligibility notice</b><p>{skipExplanation(view.restriction)}</p><details><summary>Screening evidence</summary>{view.restriction}</details><p>You can still prepare and download a resume for this posting.</p></div>}
          <section className="panel studio-result">
            <h2>{view.job.company} · {view.job.role}</h2>
            <ScoreCard before={view.score_base} />
            <div className="scores">{Object.entries(view.match.scores).sort((a, b) => b[1].pct - a[1].pct).map(([key, score]) => <span className={`score ${key === view.match.chosen ? "on" : ""}`} key={key}>{resumeLabel(key, view.match.labels)} {pctText(score.pct)}{key === view.match.chosen && <em>selected</em>}</span>)}</div>
            <p className="muted small">Skills coverage of <b>{resumeLabel(view.match.chosen, view.match.labels)}</b>: <span className={`matchpill ${matchTone(view.match.pct)}`}>{pctText(view.match.pct)}</span> (exact wording {pctText(view.match.exact_pct)}). Scores measure how well your resume covers the posting; they don't predict hiring outcomes.</p>
          </section>

          <section className="panel studio-result">
            <details open={!view.mode}>
              <summary><h2 className="inline">Pick the skills to target</h2></summary>
              {view.keywords.length === 0 ? <p className="small">Your resume already uses every skill in the posting.</p> : GROUPS.map(([group, title, hint]) => {
                const ks = view.keywords.filter((k) => k.group === group);
                return ks.length ? (
                  <div key={group} className="keyword-group">
                    <h3>{title}</h3>
                    <p className="small muted">{hint}</p>
                    <div className="row wrap">
                      {ks.map((k) => (
                        <label key={k.term} className="check" title={k.declined ? "You said you don't have this" : k.category}>
                          <input type="checkbox" checked={targets.includes(k.term)} disabled={!!busy || k.declined} onChange={() => toggle(k.term)} />
                          {k.term}{k.required && <em className="req">required</em>}{k.declined && <small className="muted"> (you said no)</small>}
                        </label>
                      ))}
                    </div>
                  </div>
                ) : null;
              })}
              {pdfRebuild && (
                <div className="studio-pdf-review">
                  <h3>Review the content extracted from your PDF</h3>
                  <p className="muted small">Check your name, contact details, experience, education, dates and reading order. If anything is missing or out of order, upload a corrected PDF or an editable source in My files and analyze again.</p>
                  <textarea readOnly rows={12} aria-label="Content extracted from your PDF" value={view.edits.extracted_text ?? ""} />
                  <label className="studio-edit"><input type="checkbox" checked={extractionConfirmed} disabled={!!busy} onChange={(e) => setExtractionConfirmed(e.target.checked)} /><span>I reviewed the extracted content and it is complete and in the correct order.</span></label>
                </div>
              )}
              {!view.bank_ready && <p className="small muted">Your experience bank is nearly empty, so only your Skills lines can change. Import your resumes on the Experience bank tab to have bullets rewritten too.</p>}
              {canGenerate ? (
                <button className="btn primary" disabled={!!busy || (pdfRebuild && !extractionConfirmed)} onClick={generate}>
                  {busy === "generate" ? "Generating…" : view.mode ? "Generate again with these" : view.bank_ready ? `Generate tailored resume (${targets.length} skill${targets.length === 1 ? "" : "s"})` : targets.length ? `Add ${targets.length} skill${targets.length === 1 ? "" : "s"} to my resume` : "Export my resume unchanged"}
                </button>
              ) : (
                <p className="banner warn">Add the selected resume's Word (.docx) or LaTeX (.tex) source in My files, or import your resumes into the experience bank, then analyze again.</p>
              )}
            </details>
          </section>

          {view.mode ? (
            <StudioDraft view={view} setView={show} />
          ) : (
            <section className="panel studio-result">
              <div className="row wrap">
                <h2>Check your resume first</h2>
                <div className="spacer" />
                <button className="btn" disabled={!!busy} onClick={() => run("diagnose", "/api/resume-studio/diagnose", { draft_id: view.draft_id })}>{busy === "diagnose" ? "Checking…" : "Check"}</button>
              </div>
              {view.diagnosis && (view.diagnosis.length === 0 ? <p className="small">Nothing to flag.</p> : (
                <ul className="studio-issues">{view.diagnosis.map((i) => <li key={i.id} className={i.severity}><span>{i.text}</span></li>)}</ul>
              ))}
              {view.diagnosis && view.diagnosis.some((i) => i.kind !== "missing_summary" && i.kind !== "missing_skills") && <p className="small muted">Generate a tailored resume to fix these line by line.</p>}
            </section>
          )}
        </>
      )}
    </div>
  );
}
