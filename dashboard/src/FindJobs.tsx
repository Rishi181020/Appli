import { useCallback, useEffect, useMemo, useState } from "react";
import { agent, matchTone, pctText, resumeLabel, timeAgo } from "./lib";
import { IconAlert, IconCheck, IconExternal, IconSearch, IconX } from "./Icons";

type Prefs = {
  titles: string[];
  levels: string[];
  locations: string[];
  days: number;
  exclude: string[];
  sources: string[];
  companies: string[];
  score_top: number;
};
type Result = {
  key: string;
  company: string;
  title: string;
  location: string;
  url: string;
  ats: string;
  source: string;
  posted: string | null;
  sponsorship: string;
  match_pct: number | null;
  resume: string | null;
  screened: string | null;
  fillable: boolean;
  scored: boolean;
  added: boolean;
  match: { labels?: Record<string, string> } | null;
};
type Found = { searched_at?: string; counts?: Record<string, number>; results?: Result[] };
type TaskStatus = { running: boolean; current: number; total: number; lines: string[]; exit: number | null };
type FindState = { status: TaskStatus; prefs: Prefs; found: Found };

const LEVELS = [
  { key: "intern", label: "Internships" },
  { key: "new_grad", label: "New grad" },
  { key: "entry", label: "Entry level" },
];
const SOURCES = [
  { key: "boards", label: "Company job boards", hint: "Greenhouse, Lever, Ashby and Workday boards of the companies in your jobs, plus any you add below" },
  { key: "lists", label: "New-grad / intern lists", hint: "SimplifyJobs' community lists on GitHub, updated daily" },
];

function Chips({ values, onChange, placeholder }: { values: string[]; onChange: (v: string[]) => void; placeholder: string }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const v = draft.trim();
    if (v && !values.some((x) => x.toLowerCase() === v.toLowerCase())) onChange([...values, v]);
    setDraft("");
  };
  return (
    <div className="chips-input">
      {values.map((v) => (
        <span key={v} className="chip-tag removable">
          {v}
          <button onClick={() => onChange(values.filter((x) => x !== v))} aria-label={`Remove ${v}`}>
            <IconX />
          </button>
        </span>
      ))}
      <input
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add();
          }
        }}
        onBlur={add}
      />
    </div>
  );
}

const daysAgo = (iso: string | null) => (iso ? timeAgo(`${iso}T12:00:00Z`) : "–");

export default function FindJobs() {
  const [state, setState] = useState<FindState | null>(null);
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [showPrefs, setShowPrefs] = useState(false);
  const [onlyFillable, setOnlyFillable] = useState(false);
  const [hideScreened, setHideScreened] = useState(true);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await agent<FindState>("/api/find");
      setState(s);
      setPrefs((p) => p ?? s.prefs);
      setError(null);
      if (!s.found.results) setShowPrefs(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const running = !!state?.status.running;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [running, load]);

  const set = (p: Partial<Prefs>) => setPrefs((cur) => (cur ? { ...cur, ...p } : cur));
  const toggle = (list: string[], key: string) => (list.includes(key) ? list.filter((x) => x !== key) : [...list, key]);

  async function suggest() {
    setBusy(true);
    try {
      const r = await agent<{ titles: string[] }>("/api/find/suggest", {});
      if (prefs) set({ titles: [...new Set([...prefs.titles, ...r.titles])] });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
    setBusy(false);
  }

  async function search() {
    if (!prefs) return;
    setBusy(true);
    setMsg(null);
    try {
      await agent("/api/find/prefs", { prefs });
      await agent("/api/find", {});
      setPicked(new Set());
      setShowPrefs(false);
      await load();
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
    setBusy(false);
  }

  async function addPicked() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await agent<{ inserted: number; already_present: number }>("/api/find/add", { keys: [...picked] });
      setMsg({ ok: true, text: `Added ${r.inserted} job(s) to your queue${r.already_present ? ` (${r.already_present} were already there)` : ""}.` });
      setPicked(new Set());
      await load();
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
    setBusy(false);
  }

  const rows = useMemo(
    () =>
      (state?.found.results ?? []).filter((r) => (!onlyFillable || r.fillable) && (!hideScreened || !r.screened)),
    [state, onlyFillable, hideScreened]
  );
  const screenedCount = (state?.found.results ?? []).filter((r) => r.screened).length;
  const pickable = rows.filter((r) => !r.added && !r.screened);

  if (error) return <div className="page"><div className="banner error">{error}</div></div>;
  if (!state || !prefs) return <div className="page"><div className="skeleton hero-skel" /></div>;

  const counts = state.found.counts ?? {};
  const last = state.status.lines[state.status.lines.length - 1] ?? "";

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Find jobs</h1>
          <p className="muted">
            Searches company job boards and the new-grad / intern lists with your preferences, skips anything already in your jobs, screens
            the best matches for sponsorship, citizenship and clearance, and scores them against your resumes. You pick what to add.
          </p>
        </div>
        <div className="head-actions">
          <button className="btn" onClick={() => setShowPrefs((s) => !s)}>
            {showPrefs ? "Hide preferences" : "Search preferences"}
          </button>
          <button className="btn primary" disabled={busy || running || !prefs.titles.length} onClick={search}>
            <IconSearch /> {running ? "Searching…" : "Search"}
          </button>
        </div>
      </header>

      {showPrefs && (
        <section className="panel pf">
          <div className="pf-grid">
            <div className="pf-field wide">
              <span>
                Job titles <em className="req">required</em>{" "}
                <button className="btn ghost small-btn" disabled={busy} onClick={suggest}>
                  Suggest from my profile
                </button>
              </span>
              <Chips values={prefs.titles} onChange={(titles) => set({ titles })} placeholder="Software Engineer, then Enter" />
              <small className="muted">A posting matches when its title contains every word of one of these (ML = Machine Learning, Developer = Engineer).</small>
            </div>
            <div className="pf-field">
              <span>Level</span>
              <div className="row wrap">
                {LEVELS.map((l) => (
                  <label key={l.key} className="check">
                    <input type="checkbox" checked={prefs.levels.includes(l.key)} onChange={() => set({ levels: toggle(prefs.levels, l.key) })} />
                    {l.label}
                  </label>
                ))}
              </div>
            </div>
            <label className="pf-field">
              <span>Posted within</span>
              <select value={prefs.days} onChange={(e) => set({ days: Number(e.target.value) })}>
                {[3, 7, 14, 30, 60, 90].map((d) => (
                  <option key={d} value={d}>
                    {d} days
                  </option>
                ))}
              </select>
            </label>
            <div className="pf-field wide">
              <span>Locations</span>
              <Chips values={prefs.locations} onChange={(locations) => set({ locations })} placeholder="Empty = anywhere in your country. e.g. California, Seattle, Remote" />
            </div>
            <div className="pf-field wide">
              <span>Skip titles with these words</span>
              <Chips values={prefs.exclude} onChange={(exclude) => set({ exclude })} placeholder="senior, staff…" />
            </div>
            <div className="pf-field wide">
              <span>Search</span>
              <div className="row wrap">
                {SOURCES.map((s) => (
                  <label key={s.key} className="check" title={s.hint}>
                    <input type="checkbox" checked={prefs.sources.includes(s.key)} onChange={() => set({ sources: toggle(prefs.sources, s.key) })} />
                    {s.label}
                  </label>
                ))}
              </div>
            </div>
            <label className="pf-field wide">
              <span>More companies to search (careers links on Greenhouse, Lever, Ashby or Workday, one per line)</span>
              <textarea
                rows={3}
                value={prefs.companies.join("\n")}
                onChange={(e) => set({ companies: e.target.value.split("\n") })}
                placeholder={"https://job-boards.greenhouse.io/anthropic\nhttps://jobs.ashbyhq.com/openai"}
              />
            </label>
            <label className="pf-field">
              <span>Screen and score the top</span>
              <input type="number" min={5} max={150} value={prefs.score_top} onChange={(e) => set({ score_top: Number(e.target.value) })} />
              <small className="muted">Each costs a fraction of a cent (Jev + the fast model).</small>
            </label>
          </div>
        </section>
      )}

      {running && (
        <div className="banner ok-b">
          <span className="spin" /> {state.status.total ? `Scoring ${state.status.current}/${state.status.total}: ` : ""}
          {last.replace(/^\[\d+\/\d+\]\s*/, "")}
        </div>
      )}
      {!running && state.status.exit != null && state.status.exit !== 0 && (
        <div className="banner error">
          The search stopped with an error: {last}
        </div>
      )}
      {msg && (
        <div className={`banner ${msg.ok ? "ok-b" : "error"}`}>
          {msg.ok ? <IconCheck /> : <IconAlert />} {msg.text}
        </div>
      )}

      {state.found.results ? (
        <section className="panel">
          <div className="row wrap">
            <h2>{state.found.results.length} relevant posting(s)</h2>
            <span className="small muted">
              searched {state.found.searched_at ? timeAgo(state.found.searched_at) : ""} · read {counts.fetched ?? 0}
              {counts["already in your jobs"] ? ` · ${counts["already in your jobs"]} already in your jobs` : ""}
            </span>
            <div className="spacer" />
            <label className="check">
              <input type="checkbox" checked={onlyFillable} onChange={() => setOnlyFillable((v) => !v)} /> Only sites Appli fills
            </label>
            <label className="check">
              <input type="checkbox" checked={hideScreened} onChange={() => setHideScreened((v) => !v)} /> Hide screened out ({screenedCount})
            </label>
          </div>
          <div className="preview-table find-table">
            <table>
              <thead>
                <tr>
                  <th>
                    <input
                      type="checkbox"
                      aria-label="Pick all"
                      checked={pickable.length > 0 && pickable.every((r) => picked.has(r.key))}
                      onChange={(e) => setPicked(e.target.checked ? new Set(pickable.map((r) => r.key)) : new Set())}
                    />
                  </th>
                  <th>Match</th>
                  <th>Company</th>
                  <th>Role</th>
                  <th>Location</th>
                  <th>Posted</th>
                  <th>Site</th>
                  <th>From</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} className={r.added ? "added" : r.screened ? "screened" : ""}>
                    <td>
                      {r.added ? (
                        <IconCheck />
                      ) : (
                        <input
                          type="checkbox"
                          disabled={!!r.screened}
                          checked={picked.has(r.key)}
                          onChange={() =>
                            setPicked((p) => {
                              const n = new Set(p);
                              if (n.has(r.key)) n.delete(r.key);
                              else n.add(r.key);
                              return n;
                            })
                          }
                        />
                      )}
                    </td>
                    <td>
                      {r.screened ? (
                        <span className="small bad-text" title={r.screened}>
                          screened out
                        </span>
                      ) : r.match_pct != null ? (
                        <span className={`matchpill ${matchTone(r.match_pct)}`} title={`Best resume: ${resumeLabel(r.resume ?? "", r.match?.labels)}`}>
                          {pctText(r.match_pct)} {resumeLabel(r.resume ?? "", r.match?.labels)}
                        </span>
                      ) : (
                        <span className="small muted">{r.scored ? "no text" : "not scored"}</span>
                      )}
                    </td>
                    <td>{r.company}</td>
                    <td className="ellipsis" title={r.screened ?? r.title}>
                      <a href={r.url} target="_blank" rel="noreferrer">
                        {r.title} <IconExternal />
                      </a>
                      {r.screened && <div className="small bad-text ellipsis">{r.screened}</div>}
                    </td>
                    <td className="ellipsis" title={r.location}>
                      {r.location || "–"}
                    </td>
                    <td>{daysAgo(r.posted)}</td>
                    <td>{r.fillable ? r.ats : <span className="muted">{r.ats} (by hand)</span>}</td>
                    <td className="small muted">{r.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row">
            <button className="btn primary" disabled={busy || picked.size === 0} onClick={addPicked}>
              Add {picked.size || ""} to my jobs
            </button>
            <span className="small muted">Added jobs join your queue; better matches are filled first.</span>
          </div>
        </section>
      ) : (
        !running && (
          <div className="empty">
            <div className="empty-art">
              <IconSearch />
            </div>
            <p>Set your job titles, then press Search.</p>
          </div>
        )
      )}
    </div>
  );
}
