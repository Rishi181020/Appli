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
  const [status, setStatus] = useState<Status | "all">("ready_for_review");
  const [tier, setTier] = useState("all");
  const [ats, setAts] = useState("all");
  const [q, setQ] = useState("");
  const [matchFilter, setMatchFilter] = useState("all");
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
  const atsList = useMemo(() => [...new Set(jobs.map((j) => j.ats ?? "none"))].sort(), [jobs]);

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
        (status === "all" ? j.status !== "skipped" : j.status === status) && // skipped only via their own chip
        (tier === "all" || String(j.fit_tier) === tier) &&
        (ats === "all" || (j.ats ?? "none") === ats) &&
        (matchFilter === "all" ||
          (matchFilter === "below" && j.match_pct != null && j.match_pct < MATCH_TARGET) ||
          (matchFilter === "above" && j.match_pct != null && j.match_pct >= MATCH_TARGET) ||
          (matchFilter === "unknown" && j.match_pct == null)) &&
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
  }, [jobs, status, tier, ats, q, matchFilter, sort]);

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
        <button className="btn primary" onClick={() => setAdding(true)}>
          + Add jobs
        </button>
      </header>

      {(adding || (!jobs.length && !error)) && (
        <section className="panel">
          {!jobs.length && <h2>Add the jobs you want to apply to</h2>}
          <AddJobs onDone={() => load()} onClose={jobs.length ? () => setAdding(false) : undefined} />
        </section>
      )}

      {error && <div className="banner error">Couldn’t load jobs: {error}</div>}

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
          <button className={status === "all" ? "on" : ""} onClick={() => setStatus("all")}>
            All <span>{jobs.length - (counts.submitted ?? 0) - (counts.skipped ?? 0)}</span>
          </button>
          {ACTIVE_STATUSES.map((s) => (
            <button key={s} className={status === s ? "on" : ""} onClick={() => setStatus(s)}>
              {STATUS_LABEL[s]} <span>{counts[s] ?? 0}</span>
            </button>
          ))}
        </div>
        <div className="tools">
          <div className="search">
            <IconSearch />
            <input placeholder="Search company or role" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <select value={tier} onChange={(e) => setTier(e.target.value)} aria-label="Fit tier">
            <option value="all">All tiers</option>
            <option value="1">Tier 1 · best fit</option>
            <option value="2">Tier 2 · new grad</option>
            <option value="3">Tier 3 · entry-level</option>
          </select>
          <select value={ats} onChange={(e) => setAts(e.target.value)} aria-label="Site">
            <option value="all">All sites</option>
            {atsList.map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
          <select value={matchFilter} onChange={(e) => setMatchFilter(e.target.value)} aria-label="Match">
            <option value="all">Any match</option>
            <option value="below">Below {pctText(MATCH_TARGET)}</option>
            <option value="above">{pctText(MATCH_TARGET)} or more</option>
            <option value="unknown">Not scored yet</option>
          </select>
          <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} aria-label="Sort">
            <option value="tier">Sort: fit tier</option>
            <option value="match_desc">Sort: match % high → low</option>
            <option value="match_asc">Sort: match % low → high</option>
          </select>
          <button className="btn ghost icon-btn" onClick={load} title="Refresh">
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
              {status === "ready_for_review" ? "Press Run to fill your next batch." : "No applications match these filters."}
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
