import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AgentStatus,
  ACTIVE_STATUSES,
  Job,
  MATCH_TARGET,
  STATUS_LABEL,
  Status,
  agent,
  fetchAllJobs,
  hue,
  matchTone,
  pctText,
  timeAgo,
} from "./lib";
import RunCard, { getGoal } from "./RunCard";
import Drawer from "./Drawer";
import AddJobs from "./AddJobs";
import PreScreen from "./PreScreen";
import Activity from "./Activity";
import MultiSelect from "./MultiSelect";
import { IconChevron, IconPlay, IconRefresh, IconSearch } from "./Icons";

const sameDay = (iso: string) => new Date(iso).toDateString() === new Date().toDateString();

export function Avatar({ name, size = 34 }: { name: string; size?: number }) {
  const h = hue(name);
  return (
    <div
      className="avatar"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.4,
        background: `linear-gradient(135deg, hsl(${h} 70% 55%), hsl(${(h + 40) % 360} 70% 45%))`,
      }}
    >
      {name.trim().charAt(0).toUpperCase()}
    </div>
  );
}

/** A row with a pick box in front: the number shows the order it will run in. */
function PickRow({ j, order, onToggle, children }: { j: Job; order: number; onToggle: () => void; children: React.ReactNode }) {
  const pickable = j.status !== "submitted" && j.status !== "filling";
  return (
    <div className={`pickrow ${order ? "picked" : ""}`}>
      <button
        className="pick"
        onClick={onToggle}
        disabled={!pickable}
        aria-pressed={order > 0}
        title={pickable ? (order ? "Remove from the manual queue" : "Add to the manual queue") : "Already submitted or being filled"}
      >
        {order ? order : ""}
      </button>
      {children}
    </div>
  );
}

export function JobItem({ j, selected, onClick }: { j: Job; selected: boolean; onClick: () => void }) {
  const need = (j.unanswered ?? []).filter((u) => u.required).length;
  return (
    <button className={`item ${selected ? "sel" : ""}`} onClick={onClick}>
      <Avatar name={j.company} />
      <div className="who">
        <div className="company">{j.company}</div>
        <div className="role">{j.role}</div>
      </div>
      <div className="meta">
        <span className={`matchpill ${matchTone(j.match_pct)}`} title="Best resume's match with the posting">
          {pctText(j.match_pct)}
        </span>
        <span className={`tierpill t${j.fit_tier ?? 0}`}>T{j.fit_tier ?? "–"}</span>
        <span className="chip-tag">{j.ats}</span>
      </div>
      <div className="state">
        {need > 0 && j.status !== "submitted" && <span className="needpill">{need} need you</span>}
        <span className={`badge ${j.status}`}>
          <i />
          {STATUS_LABEL[j.status]}
        </span>
      </div>
      <div className="when">{timeAgo(j.updated_at)}</div>
      <IconChevron />
    </button>
  );
}

export default function JobsView({ onOpenApplied }: { onOpenApplied: () => void }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // multi-select filters: an empty list means "all"
  const [statuses, setStatuses] = useState<Status[]>(["ready_for_review"]);
  const setStatus = (s: Status) => setStatuses([s]); // the KPI cards jump to one status
  const toggleStatus = (s: Status) => setStatuses((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));
  const [tiers, setTiers] = useState<string[]>([]);
  const [sites, setSites] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [matches, setMatches] = useState<string[]>([]);
  const [sort, setSort] = useState<"tier" | "match_desc" | "match_asc">("tier");
  const [openId, setOpenId] = useState<number | null>(null);
  // Manual queue: jobs ticked in the list, run in exactly this order.
  const [picked, setPicked] = useState<number[]>([]);
  const [runningPicked, setRunningPicked] = useState(false);
  const [pickError, setPickError] = useState<string | null>(null);
  const togglePick = (id: number) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length >= 100 ? p : [...p, id]));
  const [adding, setAdding] = useState(false);
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [offline, setOffline] = useState(false);
  const wasRunning = useRef(false);

  const load = useCallback(async () => {
    try {
      setJobs(await fetchAllJobs());
      setError(null);
    } catch (e) {
      setError((e as Error).message ?? String(e));
    }
    setLoading(false);
  }, []);

  const poll = useCallback(async () => {
    try {
      setAgentStatus(await agent<AgentStatus>("/api/status"));
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, []);

  useEffect(() => {
    load();
    poll();
  }, [load, poll]);

  // poll the agent quickly while it works, slowly otherwise; refresh jobs alongside
  const running = !!agentStatus?.running;

  async function runPicked() {
    setRunningPicked(true);
    setPickError(null);
    try {
      await agent("/api/run", { ids: picked });
      setPicked([]);
      poll();
      load();
    } catch (e) {
      setPickError((e as Error).message);
    }
    setRunningPicked(false);
  }
  useEffect(() => {
    const t = setInterval(() => {
      poll();
      if (running) load();
    }, running ? 2500 : 6000);
    return () => clearInterval(t);
  }, [running, poll, load]);
  useEffect(() => {
    if (wasRunning.current && !running) load(); // one last refresh when a run ends
    wasRunning.current = running;
  }, [running, load]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    jobs.forEach((j) => (c[j.status] = (c[j.status] ?? 0) + 1));
    return c;
  }, [jobs]);
  const submittedToday = jobs.filter((j) => j.status === "submitted" && sameDay(j.updated_at)).length;
  // counts among the jobs this page lists (not submitted), for the dropdowns
  const listed = useMemo(() => jobs.filter((j) => j.status !== "submitted"), [jobs]);
  const siteOptions = useMemo(() => {
    const c: Record<string, number> = {};
    listed.forEach((j) => (c[j.ats ?? "none"] = (c[j.ats ?? "none"] ?? 0) + 1));
    return Object.keys(c).sort().map((a) => ({ value: a, label: a === "none" ? "No link" : a, count: c[a] }));
  }, [listed]);
  const tierOptions = useMemo(() => {
    const c: Record<string, number> = {};
    listed.forEach((j) => (c[String(j.fit_tier ?? "none")] = (c[String(j.fit_tier ?? "none")] ?? 0) + 1));
    const names: Record<string, string> = { "1": "Tier 1 · best fit", "2": "Tier 2 · new grad", "3": "Tier 3 · entry-level", none: "No tier" };
    return Object.keys(c).sort().map((t) => ({ value: t, label: names[t] ?? `Tier ${t}`, count: c[t] }));
  }, [listed]);
  const matchOf = (j: Job) => (j.match_pct == null ? "unknown" : j.match_pct >= MATCH_TARGET ? "above" : j.match_pct >= 0.6 ? "mid" : "low");
  const matchOptions = useMemo(() => {
    const c: Record<string, number> = {};
    listed.forEach((j) => (c[matchOf(j)] = (c[matchOf(j)] ?? 0) + 1));
    return [
      { value: "above", label: `${pctText(MATCH_TARGET)} or more` },
      { value: "mid", label: `60% to ${pctText(MATCH_TARGET)}` },
      { value: "low", label: "Below 60%" },
      { value: "unknown", label: "Not scored yet" },
    ].map((o) => ({ ...o, count: c[o.value] ?? 0 }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listed]);
  const filtering = statuses.length > 0 || tiers.length > 0 || sites.length > 0 || matches.length > 0 || q.trim() !== "";
  const clearFilters = () => {
    setStatuses([]);
    setTiers([]);
    setSites([]);
    setMatches([]);
    setQ("");
  };

  const avgMatchToday = useMemo(() => {
    const m = jobs
      .filter((j) => ["ready_for_review", "submitted"].includes(j.status) && sameDay(j.updated_at) && j.match_pct != null)
      .map((j) => j.match_pct as number);
    return m.length ? m.reduce((a, b) => a + b, 0) / m.length : null;
  }, [jobs]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const out = jobs.filter(
      (j) =>
        j.status !== "submitted" && // applied jobs live on the Applied tab
        (statuses.length ? statuses.includes(j.status) : j.status !== "skipped") && // skipped only when picked
        (!tiers.length || tiers.includes(String(j.fit_tier ?? "none"))) &&
        (!sites.length || sites.includes(j.ats ?? "none")) &&
        (!matches.length || matches.includes(matchOf(j))) &&
        (!needle || `${j.company} ${j.role ?? ""}`.toLowerCase().includes(needle))
    );
    if (sort !== "tier") {
      const dir = sort === "match_desc" ? -1 : 1;
      out.sort((a, b) => {
        if (a.match_pct == null) return 1; // unknown match always last
        if (b.match_pct == null) return -1;
        return dir * (a.match_pct - b.match_pct);
      });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs, statuses, tiers, sites, q, matches, sort]);

  const patch = (id: number, p: Partial<Job>) => setJobs((js) => js.map((j) => (j.id === id ? { ...j, ...p } : j)));

  if (loading) {
    return (
      <div className="page">
        <div className="skeleton hero-skel" />
        <div className="skeleton row-skel" />
        <div className="skeleton row-skel" />
        <div className="skeleton row-skel" />
      </div>
    );
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Applications</h1>
          <p className="muted">
            {new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })} · goal {getGoal()} a day
          </p>
        </div>
        <div className="head-actions">
          {jobs.some((j) => j.ats === "workday" && ["queued", "needs_manual"].includes(j.status)) && <PreScreen onDone={load} />}
          <button className="btn primary" onClick={() => setAdding(true)}>
            + Add jobs
          </button>
        </div>
      </header>

      {(adding || (!jobs.length && !error)) && (
        <section className="panel">
          {!jobs.length && <h2>Add the jobs you want to apply to</h2>}
          <AddJobs onDone={() => load()} onClose={jobs.length ? () => setAdding(false) : undefined} />
        </section>
      )}

      {error && <div className="banner error">Couldn’t load jobs: {error}</div>}

      {jobs.length > 0 && <Activity jobs={jobs} goal={getGoal()} />}

      <RunCard
        status={agentStatus}
        offline={offline}
        queued={counts.queued ?? 0}
        ready={counts.ready_for_review ?? 0}
        submittedToday={submittedToday}
        onChanged={() => {
          poll();
          load();
        }}
      />

      <section className="kpis">
        <Kpi label="Ready for review" value={counts.ready_for_review ?? 0} tone="accent" onClick={() => setStatus("ready_for_review")} />
        {(counts.needs_help ?? 0) > 0 && (
          <Kpi label="Needs your help" value={counts.needs_help ?? 0} tone="warn" onClick={() => setStatus("needs_help")} />
        )}
        {(counts.submission_required ?? 0) > 0 && (
          <Kpi label="Submission required" value={counts.submission_required ?? 0} tone="warn" onClick={() => setStatus("submission_required")} />
        )}
        <Kpi label="Applied today" value={submittedToday} tone="ok" onClick={onOpenApplied} />
        <Kpi label="Applied total" value={counts.submitted ?? 0} onClick={onOpenApplied} />
        <Kpi label="In queue" value={counts.queued ?? 0} onClick={() => setStatus("queued")} />
        <Kpi label="Tailoring resume" value={counts.tailoring ?? 0} tone="purple" onClick={() => setStatus("tailoring")} />
        <Kpi label="Needs manual" value={counts.needs_manual ?? 0} tone="warn" onClick={() => setStatus("needs_manual")} />
        <Kpi
          label="Avg match today"
          text={pctText(avgMatchToday)}
          tone={avgMatchToday == null ? undefined : `match-${matchTone(avgMatchToday)}`}
          onClick={() => setSort("match_desc")}
        />
      </section>

      <section className="toolbar">
        <div className="seg" role="tablist">
          <button className={statuses.length === 0 ? "on" : ""} aria-pressed={statuses.length === 0} onClick={() => setStatuses([])}>
            All <span>{jobs.length - (counts.submitted ?? 0) - (counts.skipped ?? 0)}</span>
          </button>
          {ACTIVE_STATUSES.map((s) => (
            <button key={s} className={statuses.includes(s) ? "on" : ""} aria-pressed={statuses.includes(s)} onClick={() => toggleStatus(s)}>
              {statuses.includes(s) && statuses.length > 1 && <b className="seg-check">✓</b>}
              {STATUS_LABEL[s]} <span>{counts[s] ?? 0}</span>
            </button>
          ))}
        </div>
        <div className="tools">
          <div className="search">
            <IconSearch />
            <input placeholder="Search company or role" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <MultiSelect label="tier" allLabel="All tiers" options={tierOptions} value={tiers} onChange={setTiers} />
          <MultiSelect label="site" allLabel="All sites" options={siteOptions} value={sites} onChange={setSites} />
          <MultiSelect label="match range" allLabel="Any match" options={matchOptions} value={matches} onChange={setMatches} />
          <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} aria-label="Sort">
            <option value="tier">Sort: fit tier</option>
            <option value="match_desc">Sort: match % high → low</option>
            <option value="match_asc">Sort: match % low → high</option>
          </select>
          {filtering && (
            <button className="btn ghost small-btn" onClick={clearFilters}>
              Clear filters
            </button>
          )}
          <button className="btn ghost icon-btn" onClick={load} title="Refresh" aria-label="Refresh">
            <IconRefresh />
          </button>
        </div>
      </section>

      <div className="list">
        {rows.slice(0, 300).map((j) => (
          <PickRow key={j.id} j={j} order={picked.indexOf(j.id) + 1} onToggle={() => togglePick(j.id)}>
            <JobItem j={j} selected={openId === j.id} onClick={() => setOpenId(j.id)} />
          </PickRow>
        ))}
        {rows.length === 0 && (
          <div className="empty">
            <div className="empty-art">✦</div>
            <b>Nothing here</b>
            <span className="muted">
              {statuses.length === 1 && statuses[0] === "ready_for_review"
                ? "Press Run to fill your next batch."
                : "No applications match these filters."}
            </span>
          </div>
        )}
        {rows.length > 300 && <div className="muted small more">Showing the first 300 of {rows.length}. Narrow with filters.</div>}
      </div>

      {picked.length > 0 && (
        <div className="pickbar" role="region" aria-label="Manual queue">
          <div className="pickbar-text">
            <b>{picked.length} job{picked.length > 1 ? "s" : ""} picked</b>
            <span className="muted small">
              {picked
                .slice(0, 4)
                .map((id, i) => `${i + 1}. ${jobs.find((j) => j.id === id)?.company ?? id}`)
                .join("  ")}
              {picked.length > 4 ? `  +${picked.length - 4} more` : ""}
            </span>
            {pickError && <span className="inline-error">{pickError}</span>}
          </div>
          <button className="btn ghost small-btn" onClick={() => setPicked([])}>
            Clear
          </button>
          <button
            className="btn primary"
            disabled={runningPicked || running || offline}
            title={running ? "A run is already active" : offline ? "Open the app with start.bat first" : undefined}
            onClick={runPicked}
          >
            <IconPlay /> {runningPicked ? "Starting…" : `Run selected (${picked.length})`}
          </button>
        </div>
      )}

      {openId !== null && <Drawer id={openId} onClose={() => setOpenId(null)} onChanged={(p) => patch(openId, p)} />}
    </div>
  );
}

function Kpi({ label, value, text, tone, onClick }: { label: string; value?: number; text?: string; tone?: string; onClick: () => void }) {
  return (
    <button className={`kpi ${tone ?? ""}`} onClick={onClick}>
      <div className="num">{text ?? value}</div>
      <div className="lbl">{label}</div>
    </button>
  );
}
