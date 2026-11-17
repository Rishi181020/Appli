import { useEffect, useRef, useState } from "react";
import { Breakdown, ResumeEdits, ResumeMatch, agent, agentStream, diffWords, pctText, supabase } from "./lib";
import MatchRing, { MatchParts } from "./MatchRing";
import { IconCheck, IconRefresh, IconStop, IconX } from "./Icons";

export type Keyword = { term: string; category: string; required: boolean; group: "have" | "similar" | "missing"; declined: boolean };
export type SBullet = { bid: string; path: string; text: string; base: string | null; status: "pending" | "accepted" | "reverted"; source: string; can_revert: boolean };
export type Card = { id: string; term: string; bid: string | null; where: string; state: "open" | "keep" | "remove" | "similar" };
export type Issue = { id: string; kind: string; text: string; bid: string | null; fix: string | null; severity: string };
export type Style = { template?: string; header?: string; date_format?: string; skills_columns?: number; divider?: boolean };
export type Message = { role: "user" | "assistant"; content: string; bid?: string | null; rejected?: string };
export type StudioView = {
  draft_id: string;
  job: { company: string; role: string };
  match: ResumeMatch;
  edits: ResumeEdits;
  restriction: string | null;
  keywords: Keyword[];
  targets: string[];
  mode: "bank" | "skills" | null;
  score_base: Breakdown | null;
  score_after: Breakdown | null;
  bank_ready: boolean;
  chat: Message[];
  reviews: Card[];
  file: string | null;
  versions: { id: string; at: string; files: string[]; pct?: number | null; note?: string }[];
  style: Style;
  prompt: string;
  doc?: {
    pages: number;
    warnings: string[];
    skills: { group: string; items: string }[];
    sections: { type: string; heading: string; entries: { id: string; title: string; org: string; start: string; end: string; bullets: SBullet[] }[] }[];
  };
  diagnosis?: Issue[];
};
type ChatEvent = { type: "delta"; text: string } | { type: "change"; bid: string; state: StudioView } | { type: "rejected"; reason: string } | { type: "error"; error: string } | { type: "done" };

const STYLE_OPTIONS: [keyof Style, string, [string, string | number | boolean | undefined][]][] = [
  ["template", "Template", [["Your own", undefined], ["Standard", "standard"], ["Compact", "compact"]]],
  ["header", "Header", [["Your own", undefined], ["Centered", "center"], ["Left", "left"]]],
  ["date_format", "Dates", [["Your own", undefined], ["Sep 2025", "Mon YYYY"], ["September 2025", "Month YYYY"], ["09/2025", "MM/YYYY"], ["2025-09", "YYYY-MM"]]],
  ["skills_columns", "Skills", [["Your own", undefined], ["One column", 1], ["Two columns", 2]]],
  ["divider", "Section lines", [["Your own", undefined], ["Keep", true], ["Remove", false]]],
];

function Diff({ before, after }: { before: string | null; after: string }) {
  if (before == null) return <div className="diff"><div className="diff-side"><span className="diff-tag">New</span>{after.replace(/\*\*/g, "")}</div></div>;
  const d = diffWords(before.replace(/\*\*/g, ""), after.replace(/\*\*/g, ""));
  return (
    <div className="diff compare">
      <div className="diff-side"><span className="diff-tag">Your resume</span>{d.b.map((w, i) => <span key={i}>{w.del ? <del>{w.t}</del> : w.t} </span>)}</div>
      <div className="diff-side"><span className="diff-tag">Tailored</span>{d.a.map((w, i) => <span key={i}>{w.add ? <mark>{w.t}</mark> : w.t} </span>)}</div>
    </div>
  );
}

/** Score card: the base resume against the tailored one, on the four parts. */
export function ScoreCard({ before, after }: { before: Breakdown | null; after?: Breakdown | null }) {
  if (!before) return null;
  return (
    <div className="score-card">
      <div>
        <div className="eyebrow">{after ? "Before" : "Your resume"}</div>
        <MatchRing pct={before.pct} breakdown={before} size={64} label />
        <MatchParts breakdown={before} />
        {!after && before.reasons.length > 0 && <p className="small muted">{before.reasons.join(" · ")}</p>}
      </div>
      {after && (
        <div>
          <div className="eyebrow">Tailored</div>
          <MatchRing pct={after.pct} breakdown={after} size={64} label />
          <MatchParts breakdown={after} />
          <p className="small muted">{after.pct >= before.pct ? `+${Math.round((after.pct - before.pct) * 100)} points.` : "Lower than your resume: check what changed."} {after.reasons.join(" · ")}</p>
        </div>
      )}
    </div>
  );
}

export default function StudioDraft({ view, setView }: { view: StudioView; setView: (v: StudioView) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ bid: string; text: string } | null>(null);
  const [compare, setCompare] = useState(true);
  const [input, setInput] = useState("");
  const [focus, setFocus] = useState<string | null>(null);
  const [facts, setFacts] = useState(false);
  const [streaming, setStreaming] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [prompt, setPrompt] = useState(view.prompt);
  const [style, setStyle] = useState<Style>(view.style);
  const abort = useRef<AbortController | null>(null);
  const chatEnd = useRef<HTMLDivElement>(null);
  const bank = view.mode === "bank" && view.doc;
  const bullets = view.doc?.sections.flatMap((s) => s.entries.flatMap((e) => e.bullets)) ?? [];
  const pathOf = (bid: string | null) => bullets.find((b) => b.bid === bid)?.path ?? null;

  useEffect(() => chatEnd.current?.scrollIntoView({ block: "nearest" }), [view.chat.length, streaming]);
  useEffect(() => () => abort.current?.abort(), []);

  async function call(name: string, path: string, body: object) {
    setBusy(name);
    setError(null);
    try {
      setView(await agent<StudioView>(path, { draft_id: view.draft_id, ...body }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  const change = (bid: string, action: string, text?: string) => call(`${action}:${bid}`, "/api/resume-studio/change", { bid, action, text });
  async function savePrompt() {
    setBusy("prompt");
    setError(null);
    try {
      await agent("/api/resume-studio/prompt", { prompt });
      setView(await agent<StudioView>("/api/resume-studio/open", { draft_id: view.draft_id }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function send(message: string, bid: string | null, asFacts: boolean) {
    if (!message.trim() || streaming !== null) return;
    setError(null);
    setNote(null);
    setInput("");
    setFacts(false);
    setView({ ...view, chat: [...view.chat, { role: "user", content: message, bid }] });
    setStreaming("");
    const ctl = new AbortController();
    abort.current = ctl;
    let latest: StudioView | null = null;
    try {
      await agentStream<ChatEvent>("/api/resume-studio/chat", { draft_id: view.draft_id, message, bid, facts: asFacts }, (e) => {
        if (e.type === "delta") setStreaming((s) => (s ?? "") + e.text);
        else if (e.type === "change") latest = e.state;
        else if (e.type === "rejected") setNote(`The suggested line wasn't used: ${e.reason}`);
        else if (e.type === "error") setError(e.error);
      }, ctl.signal);
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      abort.current = null;
      setStreaming(null);
      // the server saved the conversation (and any checked edit): show its copy
      try {
        setView(latest ?? (await agent<StudioView>("/api/resume-studio/open", { draft_id: view.draft_id })));
      } catch {
        /* keep what's shown */
      }
    }
  }

  async function download() {
    setBusy("download");
    try {
      const { data } = await supabase.auth.getSession();
      const res = await fetch(`/api/resume-studio/${view.draft_id}/download`, { headers: { "X-Appli": "1", Authorization: `Bearer ${data.session?.access_token ?? ""}` } });
      if (!res.ok) throw new Error((await res.json()).error || "Download failed");
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = view.file?.split(/[\\/]/).pop() || "Resume.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const open = view.reviews.filter((c) => c.state === "open");
  return (
    <>
      <section className="panel studio-result">
        <div className="row wrap">
          <h2>Your tailored resume</h2>
          <div className="spacer" />
          <button className="btn" disabled={!!busy} onClick={() => call("version", "/api/resume-studio/version", {})}>Save as version</button>
          <button className="btn primary" disabled={!!busy} onClick={download}>{busy === "download" ? "Preparing…" : `Download ${view.file?.endsWith(".docx") ? "Word" : "PDF"}`}</button>
        </div>
        <ScoreCard before={view.score_base} after={view.score_after} />
        {view.doc?.warnings.map((w) => <p key={w} className="small warn-text">{w}</p>)}
        {error && <div className="banner error" role="alert">{error}</div>}
        {busy && busy !== "download" && <div className="banner" role="status"><span className="spinner" /> Updating your resume…</div>}
      </section>

      {open.length > 0 && (
        <section className="panel studio-result">
          <h2>Check these skills</h2>
          <p className="muted small">Your bank, profile and resumes don't show these. Your answer is saved, so you're only asked once.</p>
          {open.map((c) => (
            <div className="verify-card" key={c.id}>
              <p><b>Did you use {c.term}?</b></p>
              <p className="small muted">{c.bid ? `In: ${c.where.replace(/\*\*/g, "")}` : `Added to ${c.where}`}</p>
              <div className="row wrap">
                <button className="btn small-btn" disabled={!!busy} onClick={() => call(`verify:${c.id}`, "/api/resume-studio/verify", { card: c.id, answer: "keep" })}><IconCheck /> Yes, keep it</button>
                <button className="btn small-btn" disabled={!!busy} onClick={() => call(`verify:${c.id}`, "/api/resume-studio/verify", { card: c.id, answer: "remove" })}><IconX /> No, remove it</button>
                <button
                  className="btn ghost small-btn"
                  disabled={!!busy || !bank}
                  onClick={async () => {
                    await call(`verify:${c.id}`, "/api/resume-studio/verify", { card: c.id, answer: "similar" });
                    setFocus(c.bid);
                    setFacts(true);
                    setInput(`Here is what I actually did with ${c.term}: `);
                  }}
                >
                  I used something similar
                </button>
              </div>
            </div>
          ))}
        </section>
      )}

      {bank ? (
        <section className="panel studio-result">
          <div className="row wrap">
            <h2>Changes</h2>
            <div className="spacer" />
            <label className="check"><input type="checkbox" checked={compare} onChange={() => setCompare((c) => !c)} /> Side by side with your resume</label>
          </div>
          <p className="muted small">Every line quotes your experience bank. Accept it, revert it to your own resume's line, edit it yourself, or have it rewritten.</p>
          {view.doc!.sections.filter((s) => s.entries.length).map((s) => (
            <div key={s.type} className="studio-section">
              <h3>{s.heading}</h3>
              {s.entries.map((e) => (
                <div key={e.id} className="studio-entry">
                  <p className="small"><b>{e.title}</b>{e.org ? ` · ${e.org}` : ""} <span className="muted">{[e.start, e.end].filter(Boolean).join(" – ")}</span></p>
                  {e.bullets.map((b) => (
                    <div key={b.bid} className={`studio-bullet ${b.status}`}>
                      <div className="row wrap small muted">
                        <code>{b.path}</code>
                        <span className="chip-tag">{b.status === "accepted" ? "Accepted" : b.status === "reverted" ? "Reverted" : b.source === "chat" ? "From chat" : b.source === "you" ? "Your edit" : b.base == null ? "New" : "Rewritten"}</span>
                      </div>
                      {editing?.bid === b.bid ? (
                        <div className="studio-edit-box">
                          <textarea rows={3} value={editing.text} onChange={(ev) => setEditing({ bid: b.bid, text: ev.target.value })} />
                          <div className="row">
                            <button className="btn primary small-btn" disabled={!!busy} onClick={async () => { await change(b.bid, "edit", editing.text); setEditing(null); }}>Save</button>
                            <button className="btn ghost small-btn" onClick={() => setEditing(null)}>Cancel</button>
                          </div>
                        </div>
                      ) : compare ? (
                        <Diff before={b.base} after={b.text} />
                      ) : (
                        <p className="small">{b.text.replace(/\*\*/g, "")}</p>
                      )}
                      <div className="row wrap">
                        {b.status !== "accepted" && <button className="btn ghost small-btn" disabled={!!busy} onClick={() => change(b.bid, "accept")}><IconCheck /> Accept</button>}
                        {b.can_revert && <button className="btn ghost small-btn" disabled={!!busy} onClick={() => change(b.bid, "revert")}><IconX /> Revert</button>}
                        <button className="btn ghost small-btn" disabled={!!busy} onClick={() => setEditing({ bid: b.bid, text: b.text })}>Edit</button>
                        <button className="btn ghost small-btn" disabled={!!busy} onClick={() => change(b.bid, "regenerate")}><IconRefresh /> {busy === `regenerate:${b.bid}` ? "Rewriting…" : "Regenerate"}</button>
                        <button className="btn ghost small-btn" onClick={() => setFocus(b.bid)}>Ask about this line</button>
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ))}
          {view.doc!.skills.length > 0 && (
            <div className="studio-section">
              <h3>Skills</h3>
              {view.doc!.skills.map((g) => <p key={g.group} className="small"><b>{g.group}:</b> {g.items}</p>)}
            </div>
          )}
        </section>
      ) : (
        <section className="panel studio-result">
          <h2>Skills added</h2>
          <p className="muted small">Only your Skills lines changed; your experience, dates and layout are as they were. Build a resume from your experience bank to have bullets rewritten too.</p>
          {view.edits.edits.filter((e) => view.targets.includes(e.skill ?? "")).map((e) => (
            <div key={e.id} className="studio-bullet accepted">
              <p className="small muted">{e.context}</p>
              <Diff before={e.before} after={e.after} />
            </div>
          ))}
        </section>
      )}

      {bank && (
        <section className="panel studio-result">
          <h2>Ask for changes</h2>
          <div className="studio-chat">
            {view.chat.map((m, i) => (
              <div key={i} className={`chat-msg ${m.role}`}>
                {m.bid && <code className="small">{pathOf(m.bid) ?? "a line no longer on the page"}</code>}
                <p>{m.content || (m.role === "assistant" ? "(no reply)" : "")}</p>
                {m.rejected && <p className="small warn-text">Its edit wasn't used: {m.rejected}</p>}
              </div>
            ))}
            {streaming !== null && <div className="chat-msg assistant"><p>{streaming || <span className="spinner" />}</p></div>}
            <div ref={chatEnd} />
          </div>
          {note && <p className="small warn-text">{note}</p>}
          {focus && (
            <p className="small">
              About <code>{pathOf(focus)}</code> <button className="btn ghost small-btn" onClick={() => setFocus(null)}><IconX /> Any line</button>
            </p>
          )}
          <div className="row">
            <textarea
              className="chat-input"
              rows={2}
              value={input}
              placeholder={focus ? "e.g. make this one shorter and lead with the result" : "e.g. focus the Jio bullets on the payments work"}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input, focus, facts);
                }
              }}
            />
            {streaming !== null ? (
              <button className="btn" onClick={() => abort.current?.abort()}><IconStop /> Stop</button>
            ) : (
              <button className="btn primary" disabled={!input.trim()} onClick={() => send(input, focus, facts)}>Send</button>
            )}
          </div>
          {facts && <p className="small muted">This message is saved to your experience bank as what you did, so the rewrite can quote it.</p>}
          <details className="small">
            <summary>Your tailoring instructions (used for every resume Claude builds and every edit)</summary>
            <div className="row">
              <textarea className="grow" rows={8} maxLength={4000} value={prompt} placeholder="e.g. exactly 2 projects; for AI roles focus on RAG, agents and evaluation" onChange={(e) => setPrompt(e.target.value)} />
              <button className="btn small-btn" disabled={!!busy || prompt === view.prompt} onClick={savePrompt}>Save</button>
            </div>
          </details>
        </section>
      )}

      <section className="panel studio-result">
        <div className="row wrap">
          <h2>Check this resume</h2>
          <div className="spacer" />
          <button className="btn" disabled={!!busy} onClick={() => call("diagnose", "/api/resume-studio/diagnose", {})}>{busy === "diagnose" ? "Checking…" : view.diagnosis ? "Check again" : "Check"}</button>
        </div>
        {view.diagnosis && (view.diagnosis.length === 0 ? <p className="small">Nothing to flag.</p> : (
          <ul className="studio-issues">
            {view.diagnosis.map((i) => (
              <li key={i.id} className={i.severity}>
                <span>{i.text}</span>
                {i.fix && i.bid && <button className="btn ghost small-btn" disabled={streaming !== null} onClick={() => { setFocus(i.bid); send(i.fix!, i.bid, false); }}>Fix</button>}
              </li>
            ))}
          </ul>
        ))}
      </section>

      <section className="panel studio-result">
        <h2>Export style</h2>
        <p className="muted small">Saved for every tailored resume you build. "Your own" keeps your resume's layout.{view.edits.format === "pdf_rebuild" ? " A PDF-only resume keeps its exact layout, so these don't apply to it." : ""}</p>
        <div className="studio-style">
          {STYLE_OPTIONS.map(([key, label, options]) => (
            <label key={key} className="pf-field">
              {label}
              <select
                value={String(options.findIndex(([, v]) => v === style[key]))}
                onChange={(e) => {
                  const v = options[Number(e.target.value)][1];
                  const next = { ...style };
                  if (v === undefined) delete next[key];
                  else (next as Record<string, unknown>)[key] = v;
                  setStyle(next);
                }}
              >
                {options.map(([name], i) => <option key={name} value={i}>{name}</option>)}
              </select>
            </label>
          ))}
        </div>
        <button className="btn" disabled={!!busy || JSON.stringify(style) === JSON.stringify(view.style)} onClick={() => call("style", "/api/resume-studio/style", { style })}>
          {busy === "style" ? "Rebuilding…" : "Save and rebuild"}
        </button>
      </section>

      {view.versions.length > 0 && (
        <section className="panel studio-result">
          <h2>Versions</h2>
          <ul className="studio-versions">
            {view.versions.map((v) => (
              <li key={v.id}>
                <span>{v.at}{v.pct != null ? ` · ${pctText(v.pct)}` : ""}{v.note ? ` · ${v.note}` : ""}</span>
                <span className="small muted">{v.files.filter((f) => /\.(pdf|docx)$/.test(f)).join(", ")}</span>
                <button className="btn ghost small-btn" disabled={!!busy} onClick={() => call("restore", "/api/resume-studio/restore", { version: v.id })}>Restore</button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
