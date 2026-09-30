import { FormEvent, useEffect, useState } from "react";
import { Answer, norm, saveAnswer, supabase } from "./lib";

const SUGGESTIONS: { key: string; hint: string }[] = [
  { key: "~how did you hear|referred by|referral", hint: "e.g. LinkedIn" },
  { key: "~compensation|salary|pay expectation", hint: "e.g. Open to discuss" },
  { key: "~years of experience|years of relevant experience", hint: "e.g. 3" },
  { key: "~start date|when are you able to|when would you be|earliest you can", hint: "e.g. June 2027" },
  { key: "~relocate|relocation", hint: "e.g. Yes" },
  { key: "~notice period", hint: "e.g. 2 weeks" },
  { key: "~related to|family member|close personal relationship", hint: "Only if true for you (e.g. No)" },
  { key: "~convicted|felony|criminal", hint: "Only if true for you (e.g. No)" },
];

export default function AnswersView() {
  const [rows, setRows] = useState<Answer[]>([]);
  const [key, setKey] = useState("");
  const [answer, setAnswer] = useState("");
  const [hint, setHint] = useState("Answer");
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "manual" | "learned">("all");
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");

  async function load() {
    const { data, error } = await supabase.from("answers").select("*").order("id", { ascending: false });
    if (error) setError(error.message);
    else setRows(data as Answer[]);
  }
  useEffect(() => {
    load();
  }, []);

  async function add(e: FormEvent) {
    e.preventDefault();
    const k = key.trim().startsWith("~") ? key.trim() : norm(key);
    if (!k || !answer.trim()) return;
    const { error } = await saveAnswer(k, key.trim(), answer.trim());
    if (error) setError(error.message);
    else {
      setKey("");
      setAnswer("");
      setHint("Answer");
      setError(null);
      load();
    }
  }

  async function saveEdit(r: Answer) {
    const { error } = await saveAnswer(r.question_key, r.question_text ?? r.question_key, draft.trim());
    if (error) setError(error.message);
    setEditing(null);
    load();
  }

  async function remove(id: number) {
    await supabase.from("answers").delete().eq("id", id);
    load();
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Saved answers</h1>
          <p className="muted">
            Jev answers form questions from these, even when a form words a question differently. “Learned” answers come from forms
            already filled; edit one and it becomes yours. Legal and consent questions are only ever answered from what you save here.
          </p>
        </div>
      </header>

      <section className="panel">
        <p className="muted small">
          An exact question is matched word for word. A rule starting with <code>~</code> matches any question containing one of its
          phrases, separated by <code>|</code>.
        </p>
        <form className="add-row" onSubmit={add}>
          <input placeholder="Question, or a ~rule like ~how did you hear|referral" value={key} onChange={(e) => setKey(e.target.value)} />
          <input placeholder={hint} value={answer} onChange={(e) => setAnswer(e.target.value)} />
          <button className="btn primary">Save</button>
        </form>
        {error && <div className="banner error">{error}</div>}
        <div className="suggest">
          <span className="muted small">Quick rules</span>
          {SUGGESTIONS.map((s) => (
            <button
              key={s.key}
              className="chip"
              title={s.hint}
              onClick={() => {
                setKey(s.key);
                setHint(s.hint);
              }}
            >
              {s.key.slice(1).split("|")[0]}
            </button>
          ))}
        </div>
      </section>

      <div className="seg" role="tablist" style={{ alignSelf: "flex-start" }}>
        {(["all", "manual", "learned"] as const).map((f) => (
          <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
            {f === "all" ? "All" : f === "manual" ? "Yours" : "Learned"}{" "}
            <span>{f === "all" ? rows.length : rows.filter((r) => (r.source ?? "manual") === f).length}</span>
          </button>
        ))}
      </div>

      <div className="list">
        {rows
          .filter((r) => filter === "all" || (r.source ?? "manual") === filter)
          .map((r) => (
            <div className="ans" key={r.id}>
              <div className="ans-q">
                {r.question_text ?? r.question_key}
                <div className="ans-meta">
                  {r.source === "learned" ? (
                    <span className="learned" title="Learned from a form you already filled. Change it to make it yours.">Learned</span>
                  ) : (
                    <span className="mine">Yours</span>
                  )}
                  {r.uses ? <span className="muted small">used {r.uses}×</span> : null}
                </div>
              </div>
              {editing === r.id ? (
                <div className="qrow">
                  {r.options && r.options.length > 1 ? (
                    <select value={draft} onChange={(e) => setDraft(e.target.value)}>
                      {r.options.map((o) => (
                        <option key={o}>{o}</option>
                      ))}
                    </select>
                  ) : (
                    <input value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus />
                  )}
                  <button className="btn primary small-btn" onClick={() => saveEdit(r)} disabled={!draft.trim()}>
                    Save
                  </button>
                </div>
              ) : (
                <div className="ans-a">{r.answer}</div>
              )}
              <div className="ans-actions">
                <button
                  className="btn ghost small-btn"
                  onClick={() => {
                    setEditing(r.id);
                    setDraft(r.answer);
                  }}
                >
                  Edit
                </button>
                <button className="btn ghost small-btn" onClick={() => remove(r.id)}>
                  Delete
                </button>
              </div>
            </div>
          ))}
        {rows.length === 0 && (
          <div className="empty">
            <div className="empty-art">✦</div>
            <b>No saved answers yet</b>
            <span className="muted">They appear here as you answer questions on the Applications tab.</span>
          </div>
        )}
      </div>
    </div>
  );
}
