import { useCallback, useEffect, useMemo, useState } from "react";
import { Breakdown, Quals, agent, resumeLabel, timeAgo } from "./lib";
import { IconAlert, IconCheck, IconExternal, IconHeart, IconSearch, IconX } from "./Icons";
import MatchRing from "./MatchRing";

type Prefs = {
  titles: string[];
  levels: string[];
  locations: string[];
  days: number;
  exclude: string[];
  sources: string[];
  companies: string[];
  score_top: number;
  work_models: string[];
  seniorities: string[];
  company_sizes: string[];
  skip: string[];
  sponsor_only: boolean;
  exclude_companies: string[];
};
type Feedback = "liked" | "ignored" | "applied";
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
  rank: number;
  h1b: number | null;
  feedback: Feedback | null;
  match: { labels?: Record<string, string>; breakdown?: Breakdown | null; terms?: { term: string }[]; quals?: Quals } | null;
};
type Found = {
  searched_at?: string;
  counts?: Record<string, number>;
  results?: Result[];
  feedback?: Partial<Record<Feedback, Record<string, { company: string; title: string; terms: string[] }>>>;
  h1b_loaded?: boolean;
};
type TaskStatus = { running: boolean; current: number; total: number; lines: string[]; exit: number | null };
type FindState = { status: TaskStatus; prefs: Prefs; found: Found };
type Sort = "relevance" | "recent" | "match";

const LEVELS = [
  { key: "intern", label: "Internships" },
  { key: "new_grad", label: "New grad" },
  { key: "entry", label: "Entry level" },
];
const SOURCES = [
  { key: "boards", label: "Company job boards", hint: "Greenhouse, Lever, Ashby and Workday boards of the companies in your jobs, plus any you add below" },
  { key: "lists", label: "New-grad / intern lists", hint: "SimplifyJobs' community lists on GitHub, updated daily" },
  { key: "remote", label: "Remote job boards", hint: "Remote OK, We Work Remotely, Remotive, Working Nomads, Jobspresso, Landing.jobs and PowerToFly" },
];
const WORK_MODELS = [["onsite", "On-site"], ["hybrid", "Hybrid"], ["remote", "Remote"]];
const SENIORITIES = [["intern", "Intern"], ["entry", "Entry"], ["mid", "Mid"], ["senior", "Senior"], ["lead", "Lead"]];
const SIZES = [["startup", "Startup"], ["mid", "Mid-size"], ["large", "Large"]];
const SCREENS = [["no_sponsor", "No visa sponsorship"], ["citizen", "US citizens only"], ["clearance", "Security clearance"]];

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

function Checks({ options, values, onChange }: { options: string[][]; values: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="row wrap">
      {options.map(([key, label]) => (
        <label key={key} className="check">
          <input type="checkbox" checked={values.includes(key)} onChange={() => onChange(values.includes(key) ? values.filter((x) => x !== key) : [...values, key])} />
          {label}
        </label>
      ))}
    </div>
  );
}

const daysAgo = (iso: string | null) => (iso ? timeAgo(`${iso}T12:00:00Z`) : "–");
const overall = (r: Result) => r.match?.breakdown?.pct ?? r.match_pct;
const termsOf = (r: Result) => new Set((r.match?.terms ?? []).map((t) => t.term.toLowerCase()));
/** Jaccard overlap of two postings' extracted skills: how "similar jobs" are ranked. */
function jaccard(a: Set<string>, b: Set<string>) {
  if (!a.size || !b.size) return 0;
  let both = 0;
  a.forEach((x) => (both += b.has(x) ? 1 : 0));
  return both / (a.size + b.size - both);
}

export default function FindJobs() {
  const [state, setState] = useState<FindState | null>(null);
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [showPrefs, setShowPrefs] = useState(false);
  const [onlyFillable, setOnlyFillable] = useState(false);
  const [hideScreened, setHideScreened] = useState(true);
  const [showHidden, setShowHidden] = useState(false);
  const [sort, setSort] = useState<Sort>("relevance");
  const [similarTo, setSimilarTo] = useState<string | null>(null);
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
      setSimilarTo(null);
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
      const r = await agent<{ inserted: number; already_present: number; scoring?: boolean }>("/api/find/add", { keys: [...picked] });
      setMsg({ ok: true, text: `Added ${r.inserted} job(s) to your queue${r.already_present ? ` (${r.already_present} were already there)` : ""}.${r.scoring ? " The ones not scored yet are being scored in the background." : ""}` });
      setPicked(new Set());
      await load();
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
    setBusy(false);
  }

  async function feedback(r: Result, action: "like" | "ignore" | "apply") {
    const on = { like: "liked", ignore: "ignored", apply: "applied" }[action] === r.feedback;
    try {
      const out = await agent<{ found: Found }>("/api/find/feedback", { key: r.key, action: on ? "clear" : action });
      setState((s) => (s ? { ...s, found: out.found } : s));
      if (action === "ignore" && !on) setPicked((p) => { const n = new Set(p); n.delete(r.key); return n; });
      if (similarTo === r.key && on) setSimilarTo(null);
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
  }

  const all = state?.found.results ?? [];
  const similarBase = similarTo ? all.find((r) => r.key === similarTo) : undefined;
  const rows = useMemo(() => {
    let list = all.filter((r) => (!onlyFillable || r.fillable) && (!hideScreened || !r.screened) && (showHidden || r.feedback !== "ignored"));
    if (similarBase) {
      const base = termsOf(similarBase);
      return list
        .filter((r) => r.key !== similarBase.key)
        .map((r) => ({ r, s: jaccard(base, termsOf(r)) }))
        .filter((x) => x.s > 0)
        .sort((a, b) => b.s - a.s)
        .map((x) => x.r);
    }
    if (sort === "recent") list = [...list].sort((a, b) => (b.posted ?? "").localeCompare(a.posted ?? ""));
    if (sort === "match") list = [...list].sort((a, b) => (overall(b) ?? -1) - (overall(a) ?? -1));
    return list;
  }, [all, onlyFillable, hideScreened, showHidden, sort, similarBase]);
  const screenedCount = all.filter((r) => r.screened).length;
  const hiddenCount = Object.keys(state?.found.feedback?.ignored ?? {}).length;
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
              <small className="muted">
                A posting matches when its title contains every word of one of these (ML = Machine Learning, Developer = Engineer). Related titles are searched
                too: Software Engineer also finds Backend, Frontend and Full Stack Engineer.
              </small>
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
            <div className="pf-field">
              <span>Work model</span>
              <Checks options={WORK_MODELS} values={prefs.work_models} onChange={(work_models) => set({ work_models })} />
            </div>
            <div className="pf-field">
              <span>Seniority</span>
              <Checks options={SENIORITIES} values={prefs.seniorities} onChange={(seniorities) => set({ seniorities })} />
            </div>
            <div className="pf-field">
              <span>Company size</span>
              <Checks options={SIZES} values={prefs.company_sizes} onChange={(company_sizes) => set({ company_sizes })} />
              <small className="muted">None ticked = any. Work model, seniority and size are read from each posting, so they apply to the scored ones.</small>
            </div>
            <div className="pf-field">
              <span>Also skip postings that require</span>
              <Checks options={SCREENS} values={prefs.skip} onChange={(skip) => set({ skip })} />
              <label className="check">
                <input type="checkbox" checked={prefs.sponsor_only} onChange={() => set({ sponsor_only: !prefs.sponsor_only })} /> Only companies that sponsor H-1B visas
              </label>
              {!state.found.h1b_loaded && (
                <small className="muted">
                  For past sponsorship, download an LCA disclosure file from the Department of Labor (dol.gov, "OFLC performance data"), then run{" "}
                  <code>python -m runner h1b &lt;file&gt;</code> once.
                </small>
              )}
            </div>
            <div className="pf-field wide">
              <span>Skip titles with these words</span>
              <Chips values={prefs.exclude} onChange={(exclude) => set({ exclude })} placeholder="senior, staff…" />
            </div>
            <div className="pf-field wide">
              <span>Skip these companies</span>
              <Chips values={prefs.exclude_companies} onChange={(exclude_companies) => set({ exclude_companies })} placeholder="Company name, then Enter" />
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
              <small className="muted">Each costs a fraction of a cent (the fast model).</small>
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
            <h2>{similarBase ? `${rows.length} similar to ${similarBase.company} · ${similarBase.title}` : `${all.length} relevant posting(s)`}</h2>
            <span className="small muted">
              searched {state.found.searched_at ? timeAgo(state.found.searched_at) : ""} · read {counts.fetched ?? 0}
              {counts["already in your jobs"] ? ` · ${counts["already in your jobs"]} already in your jobs` : ""}
              {counts["applied"] ? ` · ${counts["applied"]} applied` : ""}
            </span>
            <div className="spacer" />
            {similarBase ? (
              <button className="btn small-btn" onClick={() => setSimilarTo(null)}>
                <IconX /> Back to all results
              </button>
            ) : (
              <label className="check">
                Sort{" "}
                <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
                  <option value="relevance">Recommended</option>
                  <option value="recent">Most recent</option>
                  <option value="match">Top matched</option>
                </select>
              </label>
            )}
            <label className="check">
              <input type="checkbox" checked={onlyFillable} onChange={() => setOnlyFillable((v) => !v)} /> Only sites Appli fills
            </label>
            <label className="check">
              <input type="checkbox" checked={hideScreened} onChange={() => setHideScreened((v) => !v)} /> Hide screened out ({screenedCount})
            </label>
            <label className="check">
              <input type="checkbox" checked={!showHidden} onChange={() => setShowHidden((v) => !v)} /> Hide not interested ({hiddenCount + (counts["not interested"] ?? 0)})
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
                  <th aria-label="Your feedback" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} className={r.added ? "added" : r.screened ? "screened" : r.feedback === "ignored" ? "ignored" : ""}>
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
                      ) : overall(r) != null ? (
                        <span title={`Best resume: ${resumeLabel(r.resume ?? "", r.match?.labels)}`}>
                          <MatchRing pct={overall(r)} breakdown={r.match?.breakdown} label />
                        </span>
                      ) : (
                        <span className="small muted">{r.scored ? "no text" : "not scored"}</span>
                      )}
                    </td>
                    <td>
                      {r.company}
                      {r.h1b ? <span className="find-badge" title="Certified H-1B filings (DOL disclosure data)">H-1B {r.h1b}</span> : r.sponsorship === "Offers Sponsorship" ? <span className="find-badge">Sponsors</span> : null}
                    </td>
                    <td className="ellipsis" title={r.screened ?? r.title}>
                      <a href={r.url} target="_blank" rel="noreferrer">
                        {r.title} <IconExternal />
                      </a>
                      {r.feedback === "applied" && <span className="find-badge">Applied</span>}
                      {r.screened ? (
                        <div className="small bad-text ellipsis">{r.screened}</div>
                      ) : r.match?.breakdown?.reasons?.length ? (
                        <div className="match-reasons">{r.match.breakdown.reasons.join(" · ")}</div>
                      ) : null}
                    </td>
                    <td className="ellipsis" title={r.location}>
                      {r.location || "–"}
                    </td>
                    <td>{daysAgo(r.posted)}</td>
                    <td>{r.fillable ? r.ats : <span className="muted">{r.ats} (by hand)</span>}</td>
                    <td className="small muted">{r.source}</td>
                    <td>
                      <span className="find-actions">
                        <button className={`btn ghost small-btn ${r.feedback === "liked" ? "on" : ""}`} title="Like" aria-pressed={r.feedback === "liked"} onClick={() => feedback(r, "like")}>
                          <IconHeart />
                        </button>
                        <button className={`btn ghost small-btn ${r.feedback === "ignored" ? "on" : ""}`} title="Not interested: hides it and shows less like it" aria-pressed={r.feedback === "ignored"} onClick={() => feedback(r, "ignore")}>
                          <IconX />
                        </button>
                        <button className={`btn ghost small-btn ${r.feedback === "applied" ? "on" : ""}`} title="I applied" aria-pressed={r.feedback === "applied"} onClick={() => feedback(r, "apply")}>
                          <IconCheck />
                        </button>
                        {r.feedback === "liked" && !!r.match?.terms?.length && !similarBase && (
                          <button className="btn ghost small-btn" title="Jobs asking for the same skills" onClick={() => setSimilarTo(r.key)}>
                            Similar
                          </button>
                        )}
                      </span>
                    </td>
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
