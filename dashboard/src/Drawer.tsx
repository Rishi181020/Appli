import { useEffect, useState } from "react";
import {
  Job,
  MATCH_TARGET,
  resumeLabel,
  STATUS_LABEL,
  Status,
  Unanswered,
  matchTone,
  norm,
  pctText,
  saveAnswer as saveAnswerRow,
  supabase,
  tailoredPdfUrl,
} from "./lib";
import { Avatar } from "./JobsView";
import { IconCheck, IconExternal, IconX } from "./Icons";

type Props = { id: number; onClose: () => void; onChanged: (p: Partial<Job>) => void };

export default function Drawer({ id, onClose, onChanged }: Props) {
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<Record<string, boolean>>({});
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    setJob(null);
    supabase
      .from("jobs")
      .select("*")
      .eq("id", id)
      .single()
      .then(({ data }) => setJob(data as Job));
  }, [id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const flash = (m: string) => {
    setToast(m);
    setTimeout(() => setToast(null), 1800);
  };

  async function setStatus(status: Status, reason: string | null = null) {
    setBusy(true);
    const { error } = await supabase.from("jobs").update({ status, status_reason: reason }).eq("id", id);
    setBusy(false);
    if (!error) {
      setJob((j) => (j ? { ...j, status, status_reason: reason } : j));
      onChanged({ status, status_reason: reason, updated_at: new Date().toISOString() });
      flash(`Marked ${STATUS_LABEL[status].toLowerCase()}`);
    }
  }

  async function saveAnswer(u: Unanswered) {
    const val = (answers[u.id] ?? "").trim();
    if (!val) return;
    const { error } = await saveAnswerRow(norm(u.label), u.label, val);
    if (!error) {
      setSaved((s) => ({ ...s, [u.id]: true }));
      flash("Saved — reused on every matching question");
    }
  }

  const open = (u: Unanswered) =>
    u.id !== "cover" && !u.reason.startsWith("checkbox") && !u.reason.startsWith("file") && !u.reason.startsWith("conditional");

  return (
    <div className="overlay" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <button className="btn ghost icon-btn close" onClick={onClose} aria-label="Close">
          <IconX />
        </button>
        {!job ? (
          <div className="muted pad">Loading…</div>
        ) : (
          <>
            <div className="d-head">
              <Avatar name={job.company} size={48} />
              <div>
                <h2>{job.company}</h2>
                <p className="d-role">{job.role}</p>
                <p className="muted small">
                  {[job.location, `Tier ${job.fit_tier ?? "–"}`, job.ats, job.resume_used && `${job.resume_used} resume`]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
            </div>

            <div className="d-status">
              <span className={`badge ${job.status}`}>
                <i />
                {STATUS_LABEL[job.status]}
              </span>
              {job.url && (
                <a className="btn small-btn" href={job.url} target="_blank" rel="noreferrer">
                  Open posting <IconExternal />
                </a>
              )}
            </div>
            {job.status_reason && <div className="banner warn">{job.status_reason}</div>}

            <div className="d-actions">
              <button className="btn primary" disabled={busy} onClick={() => setStatus("submitted")}>
                <IconCheck /> Mark submitted
              </button>
              <button className="btn" disabled={busy} onClick={() => setStatus("skipped", "Skipped by you")}>
                Skip
              </button>
              <button className="btn" disabled={busy} onClick={() => setStatus("queued")}>
                Re-queue
              </button>
              <button className="btn" disabled={busy} onClick={() => setStatus("needs_manual", "Marked for manual by you")}>
                Needs manual
              </button>
            </div>

            {job.resume_match && (
              <section className="card-s">
                <div className="sec-head">
                  <h3>Resume match</h3>
                  <span className={`matchpill big ${matchTone(job.resume_match.pct)}`}>{pctText(job.resume_match.pct)}</span>
                </div>
                <p className="small muted" style={{ margin: "2px 0 8px" }}>
                  {job.resume_match.chosen_by === "jev"
                    ? `Resume chosen by Jev (${Math.round((job.resume_match.jev_confidence ?? 0) * 100)}% confident) from the keyword coverage below.`
                    : "Resume chosen by best keyword coverage."}{" "}
                  Target {pctText(MATCH_TARGET)}.
                </p>
                <div className="scores">
                  {Object.entries(job.resume_match.scores)
                    .sort((a, b) => b[1].pct - a[1].pct)
                    .map(([k, sc]) => (
                      <span key={k} className={`score ${k === job.resume_match?.chosen ? "on" : ""}`}>
                        {resumeLabel(k, job.resume_match?.labels)} {Math.round(sc.pct * 100)}%{k === job.resume_match?.chosen && <em>used</em>}
                      </span>
                    ))}
                </div>
                {job.resume_match.exact_pct != null && (
                  <p className="small" style={{ marginTop: 8 }}>
                    Exact wording (what older ATSs see): <b>{pctText(job.resume_match.exact_pct)}</b> · counting equivalents
                    (newer ATSs): <b>{pctText(job.resume_match.pct)}</b>
                  </p>
                )}
                {(job.resume_match.equivalent ?? []).length > 0 && (
                  <p className="small muted" style={{ marginTop: 4 }}>
                    Shown under another name (half credit; tailoring can state them exactly):{" "}
                    {(job.resume_match.equivalent ?? []).join(", ")}
                  </p>
                )}
                {(() => {
                  const eq = new Set(job.resume_match?.equivalent ?? []);
                  const gaps = job.resume_match!.missing.filter((m) => !eq.has(m));
                  return gaps.length > 0 ? (
                    <p className="small muted" style={{ marginTop: 4 }}>Missing: {gaps.join(", ")}</p>
                  ) : (
                    <p className="small muted" style={{ marginTop: 4 }}>No gaps in the requirements we extracted.</p>
                  );
                })()}
                {(job.resume_match.skills_only ?? []).length > 0 && (
                  <p className="small muted" style={{ marginTop: 4 }}>
                    Only in Skills, not shown in any experience bullet: {(job.resume_match.skills_only ?? []).join(", ")}
                  </p>
                )}
                {(job.resume_match.stuffed ?? []).length > 0 && (
                  <p className="small" style={{ marginTop: 4, color: "var(--warn)" }}>
                    Mentioned more than 3 times (keyword-stuffing risk): {(job.resume_match.stuffed ?? []).join(", ")}
                  </p>
                )}
                {job.status === "tailoring" && job.resume_review !== "pending" && (
                  <p className="small" style={{ marginTop: 6 }}>Paused while a tailored resume is drafted.</p>
                )}
                {job.resume_review === "pending" && (
                  <p className="small" style={{ marginTop: 6 }}>
                    Paused: a tailored version is waiting in <b>Resume review</b>. The form is filled after you build it.
                  </p>
                )}
                {job.resume_review === "built" && job.resume_file && (
                  <p className="small" style={{ marginTop: 6 }}>
                    Tailored resume saved: <code>{job.resume_file}</code>{" "}
                    <a href={tailoredPdfUrl(job.id)} target="_blank" rel="noreferrer">Open PDF</a>
                  </p>
                )}
              </section>
            )}

            {job.unanswered && job.unanswered.length > 0 && (
              <section className="card-s">
                <h3>Left for you</h3>
                <p className="muted small">Answer once and it’s saved for every matching question in future applications.</p>
                {job.unanswered.map((u, i) => (
                  <div className="q" key={u.id + i}>
                    <div className="qtext">
                      <span className={u.required ? "req" : "opt"}>{u.required ? "Required" : "Optional"}</span> {u.label}
                      <div className="muted small">{u.reason}</div>
                    </div>
                    {open(u) && (
                      <div className="qrow">
                        {u.options && u.options.length > 0 ? (
                          <select value={answers[u.id] ?? ""} onChange={(e) => setAnswers({ ...answers, [u.id]: e.target.value })}>
                            <option value="">Choose…</option>
                            {u.options.map((o) => (
                              <option key={o}>{o}</option>
                            ))}
                          </select>
                        ) : (
                          <input
                            placeholder="Your answer"
                            value={answers[u.id] ?? ""}
                            onChange={(e) => setAnswers({ ...answers, [u.id]: e.target.value })}
                          />
                        )}
                        <button className="btn" onClick={() => saveAnswer(u)} disabled={!(answers[u.id] ?? "").trim()}>
                          {saved[u.id] ? "Saved ✓" : "Save"}
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </section>
            )}

            {job.filled_fields && (
              <section className="card-s">
                <h3>Filled by the agent</h3>
                {job.filled_fields.uploads.length > 0 && (
                  <p className="small">
                    Uploaded: <b>{job.filled_fields.uploads.join(", ")}</b>
                  </p>
                )}
                {job.filled_fields.captcha && <div className="banner warn">A captcha is on this form — you’ll need to solve it.</div>}
                <div className="kv">
                  {job.filled_fields.fields.map((f, i) => (
                    <div className="kvrow" key={i}>
                      <span className="k">{f.label}</span>
                      <span className="v">{f.value}</span>
                      <span className={`tier ${f.tier}`} title={f.tier === "jev" ? "Decided by Jev from your known answers" : undefined}>
                        {f.tier}
                        {f.confidence != null && ` ${Math.round(f.confidence * 100)}%`}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {job.cover_letter && (
              <section className="card-s">
                <div className="sec-head">
                  <h3>Cover letter</h3>
                  <button className="btn small-btn" onClick={() => navigator.clipboard.writeText(job.cover_letter ?? "").then(() => flash("Copied"))}>
                    Copy
                  </button>
                </div>
                <pre className="letter">{job.cover_letter}</pre>
              </section>
            )}

          </>
        )}
        {toast && <div className="toast">{toast}</div>}
      </aside>
    </div>
  );
}
