import { useState } from "react";
import { ResumeJob, agent, matchTone, pctText, tailoredPdfUrl, timeAgo } from "./lib";
import { Avatar } from "./JobsView";
import { IconAlert, IconCheck, IconExternal, IconRefresh, IconX } from "./Icons";

type Bullet = { text: string; evidence?: string; terms?: string[]; edited?: boolean };
type Issue = { entry: string; bullet: number | null; severity: string; type: string; problem: string; fix: string };
export type BankEdits = {
  mode: "bank";
  pages: number;
  planned_pages: number;
  plan: { pages: number; rationale: string; sections: { type: string; heading: string; entries: { id: string; bullets: number }[] }[] };
  draft: { entries: Record<string, { bullets: Bullet[]; spare?: Bullet[] }>; skills: { group: string; items: string }[] };
  entries: Record<string, { title: string; org: string; kind: string; start: string; end: string; location: string }>;
  reviews: { round: number; verdict: string; score: number | null; model: string; issues: Issue[] }[];
  dropped: { entry: string; title: string; text: string; reason: string }[];
  trimmed: { entry: string; title: string; text: string; reason: string }[];
  pct_before: number | null;
  pct_after: number | null;
  file: string;
  skills_added?: { skill: string; group: string; backed: boolean; suggested?: boolean }[];
  skills_excluded?: string[];
  warnings?: string[];
};

const splitItems = (items: string) => items.split(/,(?![^()]*\))/).map((x) => x.trim()).filter(Boolean);

export const isBank = (j: ResumeJob) => (j.resume_edits as unknown as { mode?: string } | null)?.mode === "bank";

/** A resume built for this job from the experience bank, waiting for your approval. */
export default function BankCard({ job, onChanged }: { job: ResumeJob; onChanged: () => void }) {
  const r = job.resume_edits as unknown as BankEdits;
  const built = job.resume_review === "built";
  const [bullets, setBullets] = useState<Record<string, string[]>>(() =>
    Object.fromEntries(Object.entries(r.draft.entries).map(([id, b]) => [id, b.bullets.map((x) => x.text)]))
  );
  const [skills, setSkills] = useState(r.draft.skills);
  // skills added from the posting: ticked = on the resume
  const onResume = (name: string) => skills.some((g) => splitItems(g.items).some((i) => i.toLowerCase() === name.toLowerCase()));
  const toggleSkill = (a: { skill: string; group: string }) => {
    const on = onResume(a.skill);
    setSkills((s) => {
      if (on) return s.map((g) => ({ ...g, items: splitItems(g.items).filter((i) => i.toLowerCase() !== a.skill.toLowerCase()).join(", ") }));
      const found = s.findIndex((g) => g.group === a.group);
      const at = found >= 0 ? found : s.length - 1;
      return s.map((g, j) => (j === at ? { ...g, items: [...splitItems(g.items), a.skill].join(", ") } : g));
    });
    setDirty(true);
  };
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [queued, setQueued] = useState(false);
  const [v, setV] = useState(0); // bumps the PDF link after a rebuild

  const last = r.reviews[r.reviews.length - 1];
  const remaining = (last?.issues ?? []).filter((i) => i.severity !== "low");
  const evidence = (id: string, k: number) => r.draft.entries[id]?.bullets[k]?.evidence;

  async function call(what: string, path: string, body: unknown, ok: (x: Record<string, unknown>) => string) {
    setBusy(what);
    setMsg(null);
    try {
      const x = await agent<Record<string, unknown>>(path, body);
      setMsg({ ok: true, text: ok(x) });
      return x;
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function rebuild() {
    const x = await call("rebuild", "/api/bank/rebuild", { job_id: job.id, bullets, skills }, (x) => `PDF rebuilt with your edits (${x.pages} page(s)).`);
    if (x) {
      setDirty(false);
      setV((n) => n + 1);
      onChanged();
    }
  }
  async function approve() {
    if (dirty && !(await call("rebuild", "/api/bank/rebuild", { job_id: job.id, bullets, skills }, () => "Rebuilt."))) return;
    const x = await call("approve", "/api/bank/approve", { job_id: job.id }, (x) => String(x.next));
    if (x) {
      setQueued(Boolean(x.queued));
      onChanged();
    }
  }
  async function useBase() {
    if (await call("base", "/api/resume/dismiss", { job_id: job.id }, () => "OK: it's filled with your best base resume instead.")) onChanged();
  }
  async function fillNow() {
    await call("fill", "/api/run", { ids: [job.id] }, () => "Filling it now with this resume: a browser window is opening.");
  }

  const edit = (id: string, k: number, text: string) => {
    setBullets((b) => ({ ...b, [id]: b[id].map((t, i) => (i === k ? text : t)) }));
    setDirty(true);
  };
  const remove = (id: string, k: number) => {
    setBullets((b) => ({ ...b, [id]: b[id].filter((_, i) => i !== k) }));
    setDirty(true);
  };

  return (
    <article className={`rcard bankcard ${built ? "built" : ""}`}>
      <div className="rhead">
        <Avatar name={job.company} size={40} />
        <div className="rwho">
          <div className="company">{job.company}</div>
          <div className="role">{job.role}</div>
        </div>
        <div className="rpct">
          <span className="from">{pctText(r.pct_before)}</span>
          <span className="arrow">→</span>
          <span className={`to ${matchTone(r.pct_after) === "good" ? "good" : ""}`}>{pctText(r.pct_after)}</span>
          <small>match</small>
        </div>
      </div>

      <div className="row wrap">
        <span className="chip-tag ok-tag">
          <IconCheck /> Built from your experience bank
        </span>
        <span className="chip-tag">
          {r.pages} page{r.pages > 1 ? "s" : ""}
        </span>
        {last && (
          <span className={`chip-tag ${remaining.length ? "" : "ok-tag"}`} title={`Reviewed by ${last.model}`}>
            Reviewer score {last.score ?? "–"}/10 · {remaining.length ? `${remaining.length} open point(s)` : "no open points"}
          </span>
        )}
        <span className="small muted">{timeAgo(job.updated_at)}</span>
        <div className="spacer" />
        <a className="btn small-btn" href={`${tailoredPdfUrl(job.id)}&v=${v}`} target="_blank" rel="noreferrer">
          Open PDF <IconExternal />
        </a>
      </div>

      {(r.warnings ?? []).length > 0 && (
        <div className="banner error">
          <IconAlert />
          <div>
            <b>Check this resume before you use it:</b>
            <ul className="warn-list">
              {r.warnings!.map((w, k) => (
                <li key={k}>{w}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <section className="rsec">
        <h3>Layout</h3>
        <div className="chips-row">
          {r.plan.sections.map((s, i) => (
            <span key={i} className="score">
              {i + 1}. {s.heading}
            </span>
          ))}
        </div>
        {r.plan.rationale && <p className="small muted" style={{ marginTop: 6 }}>{r.plan.rationale}</p>}
      </section>

      {r.plan.sections
        .filter((s) => s.entries.length)
        .map((s) => (
          <section key={s.heading} className="rsec">
            <h3>{s.heading}</h3>
            {s.entries
              .filter((x) => bullets[x.id])
              .map((x) => {
                const e = r.entries[x.id];
                return (
                  <div key={x.id} className="bank-entry">
                    <div className="bank-entry-head">
                      <b>{e?.org && ["experience", "research", "leadership"].includes(e.kind) ? `${e.title}, ${e.org}` : e?.title}</b>
                      <span className="small muted">{[e?.start, e?.end].filter(Boolean).join(" - ")}</span>
                    </div>
                    {bullets[x.id].map((t, k) => (
                      <div key={k} className="bank-bullet">
                        <textarea rows={2} value={t} disabled={built} onChange={(ev) => edit(x.id, k, ev.target.value)} />
                        <div className="bank-bullet-tools">
                          {evidence(x.id, k) && (
                            <button className="btn ghost small-btn" onClick={() => setOpen((o) => ({ ...o, [`${x.id}${k}`]: !o[`${x.id}${k}`] }))}>
                              {open[`${x.id}${k}`] ? "Hide source" : "Source"}
                            </button>
                          )}
                          {!built && (
                            <button className="btn ghost icon-btn" aria-label="Remove this bullet" onClick={() => remove(x.id, k)}>
                              <IconX />
                            </button>
                          )}
                        </div>
                        {open[`${x.id}${k}`] && <div className="bank-evidence">“{evidence(x.id, k)}”</div>}
                      </div>
                    ))}
                  </div>
                );
              })}
          </section>
        ))}

      <section className="rsec">
        <h3>Skills</h3>
        {skills.map((g, i) => (
          <div key={i} className="bank-skill">
            <input
              value={g.group}
              disabled={built}
              onChange={(e) => {
                setSkills((s) => s.map((x, j) => (j === i ? { ...x, group: e.target.value } : x)));
                setDirty(true);
              }}
            />
            <input
              value={g.items}
              disabled={built}
              onChange={(e) => {
                setSkills((s) => s.map((x, j) => (j === i ? { ...x, items: e.target.value } : x)));
                setDirty(true);
              }}
            />
          </div>
        ))}
        {(r.skills_added ?? []).length > 0 && (
          <div className="bank-added">
            <div className="small">
              <b>From the posting</b> <span className="muted">(ticked ones are on your resume: untick any you don't have, tick a suggestion to add it, then rebuild)</span>
            </div>
            <div className="chips-row">
              {r.skills_added!.map((a) => (
                <label key={a.skill} className={`chip-check ${a.backed ? "" : "unbacked"}`} title={a.backed ? "In your bank or profile" : "Not in your bank: keep only if true"}>
                  <input type="checkbox" checked={onResume(a.skill)} disabled={built} onChange={() => toggleSkill(a)} />
                  {a.skill}
                  {!a.backed && <span className="warn-text small"> · not in your bank</span>}
                </label>
              ))}
            </div>
          </div>
        )}
        {(r.skills_excluded ?? []).length > 0 && (
          <p className="small muted">Not added (programming languages not on your base resume): {r.skills_excluded!.join(", ")}</p>
        )}
      </section>

      {remaining.length > 0 && (
        <section className="rsec">
          <h3>Reviewer's open points on the generated draft</h3>
          <ul className="problems">
            {remaining.map((i, k) => (
              <li key={k}>
                <IconAlert />
                <div>
                  <b>{i.type}</b> · {r.entries[i.entry]?.title ?? i.entry}
                  {i.bullet != null ? ` (bullet ${i.bullet + 1})` : ""}: {i.problem}
                  {i.fix && <div className="small muted">Suggested: {i.fix}</div>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
      {(r.dropped.length > 0 || r.trimmed.length > 0) && (
        <details className="rsec">
          <summary className="small muted">
            Left out: {r.dropped.length} that failed the honesty checks, {r.trimmed.length} to fit the page
          </summary>
          <ul className="problems">
            {[...r.dropped, ...r.trimmed].map((d, k) => (
              <li key={k}>
                <IconAlert />
                <div>
                  {d.text}
                  <div className="small muted">{d.title}: {d.reason}</div>
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}

      {msg && (
        <div className={`banner ${msg.ok ? "ok-b" : "error"}`}>
          {msg.ok ? <IconCheck /> : <IconAlert />} {msg.text}
          {queued && (
            <button className="btn primary small-btn" disabled={!!busy} onClick={fillNow}>
              Fill it now
            </button>
          )}
        </div>
      )}
      {!built && (
        <footer className="ractions">
          <button className="btn primary" disabled={!!busy} onClick={approve}>
            {busy === "approve" ? "Approving…" : dirty ? "Rebuild & approve" : "Approve & use this resume"}
          </button>
          {dirty && (
            <button className="btn" disabled={!!busy} onClick={rebuild}>
              <IconRefresh /> {busy === "rebuild" ? "Rebuilding…" : "Rebuild PDF with my edits"}
            </button>
          )}
          <button className="btn ghost" disabled={!!busy} onClick={useBase}>
            Use base resume instead
          </button>
        </footer>
      )}
    </article>
  );
}
