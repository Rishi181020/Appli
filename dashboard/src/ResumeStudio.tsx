import { useState } from "react";
import { BuildResult, ResumeEdits, ResumeMatch, agent, matchTone, pctText, resumeLabel, skipExplanation, supabase } from "./lib";
import { IconDoc } from "./Icons";

type Draft = { draft_id: string; match: ResumeMatch; edits: ResumeEdits; restriction: string | null };
type Export = BuildResult & { draft_id: string; unchanged?: boolean; preserved_pdf_layout?: boolean; pages?: number };

export default function ResumeStudio() {
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [description, setDescription] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [accepted, setAccepted] = useState<string[]>([]);
  const [result, setResult] = useState<Export | null>(null);
  const [extractionConfirmed, setExtractionConfirmed] = useState(false);
  const [busy, setBusy] = useState<"analyze" | "build" | "download" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reset = () => { setDraft(null); setResult(null); setError(null); setExtractionConfirmed(false); };
  async function analyze() {
    setBusy("analyze"); reset();
    try {
      const next = await agent<Draft>("/api/resume-studio/analyze", { company, role, description });
      setDraft(next);
      setAccepted(next.edits.edits.filter(e => e.default).map(e => e.id));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  }
  async function build() {
    if (!draft) return;
    setBusy("build"); setError(null); setResult(null);
    try {
      const next = await agent<Export>("/api/resume-studio/build", { draft_id: draft.draft_id, accepted, extraction_confirmed: extractionConfirmed });
      if (!next.ok) throw new Error(next.error || "Could not build your resume");
      setResult(next);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  }
  async function download() {
    if (!result) return;
    setBusy("download"); setError(null);
    try {
      const { data } = await supabase.auth.getSession();
      const response = await fetch(`/api/resume-studio/${result.draft_id}/download`, {
        headers: { "X-Appli": "1", Authorization: `Bearer ${data.session?.access_token ?? ""}` },
      });
      if (!response.ok) throw new Error((await response.json()).error || "Download failed");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url; link.download = result.file?.split(/[\\/]/).pop() || "Resume.pdf";
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  }
  const pdfRebuild = draft?.edits.format === "pdf_rebuild";
  const canEdit = !!draft?.match.tailorable || pdfRebuild;
  return <div className="page resume-studio">
    <header className="page-head"><div><h1>Tailor resume</h1><p className="muted">Paste a job description to compare your resumes, review suggested changes and download a resume for that role.</p></div></header>
    <section className="panel studio-input">
      <div className="studio-fields">
        <label className="pf-field">Company (optional)<input maxLength={200} disabled={!!busy} value={company} onChange={e => { setCompany(e.target.value); reset(); }} placeholder="Company name" /></label>
        <label className="pf-field">Job title (optional)<input maxLength={200} disabled={!!busy} value={role} onChange={e => { setRole(e.target.value); reset(); }} placeholder="Software Engineer" /></label>
      </div>
      <label className="pf-field">Job description<textarea rows={12} maxLength={40000} disabled={!!busy} value={description} onChange={e => { setDescription(e.target.value); reset(); }} placeholder="Paste the full description, including responsibilities, required skills and qualifications…" /><small>{description.trim().length.toLocaleString()} characters · At least 300 characters</small></label>
      <button className="btn primary" disabled={!!busy || description.trim().length < 300} onClick={analyze}><IconDoc />{busy === "analyze" ? "Matching resumes and drafting changes…" : "Analyze and tailor"}</button>
    </section>
    {busy && <div className="banner" role="status"><span className="spinner" /> {busy === "analyze" ? "Checking eligibility, scoring your uploaded resumes and preparing suggested changes. This can take a minute." : busy === "build" ? "Building your reviewed resume and calculating its updated match score…" : "Preparing your download…"}</div>}
    {error && <div className="banner error" role="alert">{error}</div>}
    {draft && <>
      {draft.restriction && <div className="banner warn"><b>Eligibility notice</b><p>{skipExplanation(draft.restriction)}</p><details><summary>Screening evidence</summary>{draft.restriction}</details><p>You can still prepare and download a resume for this posting.</p></div>}
      <section className="panel studio-result">
        <h2>Your resume match</h2>
        <div className="scores">{Object.entries(draft.match.scores).sort((a, b) => b[1].pct - a[1].pct).map(([key, score]) => <span className={`score ${key === draft.match.chosen ? "on" : ""}`} key={key}>{resumeLabel(key, draft.match.labels)} {pctText(score.pct)}{key === draft.match.chosen && <em>selected</em>}</span>)}</div>
        <p>Selected resume: <b>{resumeLabel(draft.match.chosen, draft.match.labels)}</b> <span className={`matchpill ${matchTone(draft.match.pct)}`}>{pctText(draft.match.pct)}</span></p>
        <p className="muted small">Exact keyword match: {pctText(draft.match.exact_pct)}. Scores measure coverage of the posting's skills, including recognized equivalents; they do not predict hiring outcomes.</p>
        {draft.match.missing.length > 0 && <p className="small"><b>Keywords to address:</b> {draft.match.missing.join(", ")}</p>}
      </section>
      <section className="panel studio-result">
        <h2>Review suggested changes</h2>
        <p className="muted">Supported skills start selected. Select other skills only if you have them. {pdfRebuild ? "Appli edits the existing Skills section in your original PDF and keeps the resume on one page. All other sections keep their original layout. If the additions do not fit, deselect some skills and try again." : "Your experience, dates and existing layout are preserved."}</p>
        {pdfRebuild && <div className="studio-pdf-review">
          <h3>Review the content extracted from your PDF</h3>
          <p className="muted small">Check your name, contact details, experience, education, dates and reading order. If anything is missing or out of order, upload a corrected PDF or an editable source in My files and analyze again.</p>
          <textarea readOnly rows={16} aria-label="Content extracted from your PDF" value={draft.edits.extracted_text ?? ""} />
          <label className="studio-edit"><input type="checkbox" checked={extractionConfirmed} disabled={!!busy} onChange={e => setExtractionConfirmed(e.target.checked)} /><span>I reviewed the extracted content and it is complete and in the correct order.</span></label>
        </div>}
        {draft.edits.problems.map((problem, i) => <p className="small muted" key={i}>{problem.detail}</p>)}
        {draft.edits.edits.map(edit => <label className="studio-edit" key={edit.id}>
          <input type="checkbox" checked={accepted.includes(edit.id)} disabled={!!busy} onChange={() => { setAccepted(values => values.includes(edit.id) ? values.filter(id => id !== edit.id) : [...values, edit.id]); setResult(null); }} />
          <span><b>{edit.skill}</b> <small>{edit.support === "not_found" ? "Add only if true" : "Supported by your materials"}</small><p className="muted small">{edit.context}</p><p className="small">{edit.after}</p>{edit.evidence && <p className="muted small">Evidence: {edit.evidence}</p>}</span>
        </label>)}
        {canEdit ? <button className="btn primary" disabled={!!busy || (pdfRebuild && !extractionConfirmed)} onClick={build}>{busy === "build" ? "Building resume…" : accepted.length ? `Build resume with ${accepted.length} change${accepted.length === 1 ? "" : "s"}` : "Export selected resume without changes"}</button> : <p className="banner warn">Add the selected resume's Word (.docx) or LaTeX (.tex) source in My files, then analyze again to enable editing and download.</p>}
      </section>
      {result && <section className="panel studio-result" role="status"><h2>Your resume is ready</h2><p>Match: {pctText(draft.match.pct)} → <b>{pctText(result.pct_after)}</b></p><p className="muted">{result.preserved_pdf_layout ? `Your PDF stays on one page with its original section layout. ${result.added_words?.length ? "Approved keywords were added within the existing Skills section." : "No skills were added."}` : result.unchanged ? "Exported your selected resume without adding skills." : "Your approved keywords have been added to the skills section."} {result.file?.endsWith(".docx") ? "Download as Word." : "Download as PDF."}</p><button className="btn primary" disabled={!!busy} onClick={download}>Download resume</button></section>}
    </>}
  </div>;
}
