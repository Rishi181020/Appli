import { useState } from "react";
import { Me, ResumeFile, agent, fileToBase64 } from "./lib";
import { IconAlert, IconCheck, IconRefresh } from "./Icons";

type FolderStatus = Pick<Me, "folder" | "resumes" | "cover_letter" | "profile_md">;
type OnChange = (s: FolderStatus) => void;

/** A button that opens a file picker. */
function Pick({ label, accept, onFile, primary, disabled }: { label: string; accept: string; onFile: (f: File) => void; primary?: boolean; disabled?: boolean }) {
  return (
    <label className={`btn small-btn ${primary ? "primary" : ""} ${disabled ? "disabled" : ""}`}>
      {label}
      <input
        type="file"
        accept={accept}
        hidden
        disabled={disabled}
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) onFile(f);
        }}
      />
    </label>
  );
}

function ResumeCard({ r, busy, run }: { r: ResumeFile; busy: boolean; run: (fn: () => Promise<FolderStatus>) => void }) {
  const [label, setLabel] = useState(r.label);
  const [focus, setFocus] = useState(r.focus);
  const dirty = label !== r.label || focus !== r.focus;
  return (
    <div className="pf-item file-card">
      <div className="pf-grid">
        <label className="pf-field">
          <span>Label</span>
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="SWE" />
        </label>
        <div className="pf-field">
          <span>Files</span>
          <div className="file-names">
            <code>{r.pdf}</code>
            {r.tex ? (
              <span className="chip-tag ok-tag">
                <IconCheck /> LaTeX: tailoring on
              </span>
            ) : (
              <span className="chip-tag">PDF only: no tailoring</span>
            )}
          </div>
        </div>
        <label className="pf-field wide">
          <span>What it's for (Jev uses this to pick a resume for each job)</span>
          <input value={focus} onChange={(e) => setFocus(e.target.value)} placeholder="Backend and full-stack: FastAPI, React, SQL" />
        </label>
      </div>
      <div className="row wrap">
        {dirty && (
          <button className="btn primary small-btn" disabled={busy} onClick={() => run(() => agent("/api/files/resume", { key: r.key, label, focus }))}>
            Save changes
          </button>
        )}
        <Pick
          label="Replace PDF"
          accept=".pdf,application/pdf"
          disabled={busy}
          onFile={(f) => run(async () => agent("/api/files/resume", { key: r.key, label, focus, pdf: await fileToBase64(f) }))}
        />
        {r.tex ? (
          <button className="btn small-btn" disabled={busy} onClick={() => run(() => agent("/api/files/resume/remove-tex", { key: r.key }))}>
            Remove .tex
          </button>
        ) : (
          <Pick
            label="Add LaTeX (.tex)"
            accept=".tex"
            disabled={busy}
            onFile={(f) => run(async () => agent("/api/files/resume", { key: r.key, label, focus, tex: await fileToBase64(f) }))}
          />
        )}
        <div className="spacer" />
        <button
          className="btn ghost small-btn"
          disabled={busy}
          onClick={() => {
            if (confirm(`Remove the ${r.label} resume and delete its files from your folder?`))
              run(() => agent("/api/files/resume/remove", { key: r.key }));
          }}
        >
          Remove
        </button>
      </div>
    </div>
  );
}

export function ResumeManager({ me, onChange }: { me: FolderStatus; onChange: OnChange }) {
  const [label, setLabel] = useState("");
  const [pdf, setPdf] = useState<File | null>(null);
  const [tex, setTex] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function run(fn: () => Promise<FolderStatus>, ok = "Saved.") {
    setBusy(true);
    setMsg(null);
    try {
      onChange(await fn());
      setMsg({ ok: true, text: ok });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
    setBusy(false);
  }

  async function add() {
    if (!pdf) return;
    await run(async () => {
      const body: Record<string, string> = { label: label || guessLabel(pdf.name), pdf: await fileToBase64(pdf) };
      if (tex) body.tex = await fileToBase64(tex);
      return agent<FolderStatus>("/api/files/resume", body);
    }, "Resume added. Check the one-line description below: it was drafted from the resume.");
    setLabel("");
    setPdf(null);
    setTex(null);
  }

  return (
    <div className="pf-list">
      <p className="muted small">
        Add every version you use (e.g. SWE, AI, Full-stack). For each job the best-matching one is uploaded. Add the LaTeX source
        (<code>main.tex</code>) too if you have it: then missing skills can be added to a tailored copy for you to approve.
      </p>
      {me.resumes.map((r) => (
        <ResumeCard key={`${r.key}-${r.label}-${r.focus}-${r.tex}`} r={r} busy={busy} run={(fn) => run(fn)} />
      ))}
      <div className="pf-item add-card">
        <b>{me.resumes.length ? "Add another resume" : "Add your resume"}</b>
        <div className="pf-grid">
          <label className="pf-field">
            <span>Label</span>
            <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={pdf ? guessLabel(pdf.name) : "SWE"} />
          </label>
          <div className="pf-field">
            <span>Files</span>
            <div className="row wrap">
              <Pick label={pdf ? `PDF: ${pdf.name}` : "Choose PDF"} accept=".pdf,application/pdf" onFile={setPdf} primary={!pdf} />
              <Pick label={tex ? `LaTeX: ${tex.name}` : "LaTeX .tex (optional)"} accept=".tex" onFile={setTex} />
            </div>
          </div>
        </div>
        <div className="row">
          <button className="btn primary" disabled={!pdf || busy} onClick={add}>
            {busy ? "Saving…" : "Add resume"}
          </button>
        </div>
      </div>
      {msg && (
        <div className={`banner ${msg.ok ? "ok-b" : "error"}`}>
          {msg.ok ? <IconCheck /> : <IconAlert />} {msg.text}
        </div>
      )}
    </div>
  );
}

/** "Rishi Resume AI Eng.pdf" -> "AI Eng" */
function guessLabel(name: string): string {
  const base = name.replace(/\.pdf$/i, "").replace(/[_]+/g, " ").trim();
  const m = base.match(/resume[\s-]*(.+)$/i);
  return (m ? m[1] : base).trim().slice(0, 40) || "General";
}

export function CoverManager({ me, onChange }: { me: FolderStatus; onChange: OnChange }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function run(fn: () => Promise<FolderStatus>, ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      onChange(await fn());
      setMsg({ ok: true, text: ok });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
    setBusy(false);
  }

  return (
    <div className="pf-list">
      <p className="muted small">
        Optional. A cover letter you've written before (PDF, Word, LaTeX or text). New cover letters are written in your voice, using only
        facts from your profile and resume.
      </p>
      <div className="row wrap">
        {me.cover_letter ? (
          <span className="chip-tag ok-tag">
            <IconCheck /> {me.cover_letter}
          </span>
        ) : (
          <span className="muted small">No cover letter yet.</span>
        )}
        <Pick
          label={me.cover_letter ? "Replace" : "Upload cover letter"}
          accept=".pdf,.docx,.tex,.txt,.md"
          primary={!me.cover_letter}
          disabled={busy}
          onFile={(f) =>
            run(async () => agent("/api/files/cover", { filename: f.name, data: await fileToBase64(f) }), "Cover letter saved.")
          }
        />
        {me.cover_letter && (
          <button className="btn ghost small-btn" disabled={busy} onClick={() => run(() => agent("/api/files/cover/remove", {}), "Removed.")}>
            Remove
          </button>
        )}
      </div>
      {msg && (
        <div className={`banner ${msg.ok ? "ok-b" : "error"}`}>
          {msg.ok ? <IconCheck /> : <IconAlert />} {msg.text}
        </div>
      )}
    </div>
  );
}

type Check = { ok: boolean; what: string; fix: string };

function SystemCheck() {
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      setChecks((await agent<{ checks: Check[] }>("/api/health")).checks);
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }
  return (
    <div className="pf-list">
      <div className="row">
        <p className="muted small">Checks your API key, the models in .env, and LaTeX. Sends one tiny request to each model.</p>
        <div className="spacer" />
        <button className="btn small-btn" disabled={busy} onClick={go}>
          <IconRefresh /> {busy ? "Checking…" : checks ? "Check again" : "Run check"}
        </button>
      </div>
      {error && <div className="banner error">{error}</div>}
      {checks && (
        <ul className="checks">
          {checks.map((c) => (
            <li key={c.what} className={c.ok ? "ok" : "bad"}>
              {c.ok ? <IconCheck /> : <IconAlert />}
              <div>
                {c.what}
                {c.fix && <div className="small muted">{c.fix}</div>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function FilesView({ me, onChange }: { me: Me; onChange: OnChange }) {
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>My files</h1>
          <p className="muted">
            Saved on this computer in <code>{me.folder}/</code> (never uploaded anywhere, never committed).
          </p>
        </div>
      </header>
      <section className="panel">
        <h2>Resumes</h2>
        <ResumeManager me={me} onChange={onChange} />
      </section>
      <section className="panel">
        <h2>Cover letter</h2>
        <CoverManager me={me} onChange={onChange} />
      </section>
      <section className="panel">
        <h2>System check</h2>
        <SystemCheck />
      </section>
    </div>
  );
}
