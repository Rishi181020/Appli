import { useCallback, useEffect, useMemo, useState } from "react";
import { Job, LIST_COLUMNS, supabase } from "./lib";
import Drawer from "./Drawer";
import { JobItem } from "./JobsView";
import { IconRefresh, IconSearch } from "./Icons";

const dayKey = (iso: string) => new Date(iso).toDateString();

function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

export default function AppliedView() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);

  const load = useCallback(async () => {
    const out: Job[] = [];
    let cols = `${LIST_COLUMNS},match_pct`; // match_pct arrives with migration 003
    for (let from = 0; ; from += 1000) {
      const { data, error } = await supabase
        .from("jobs")
        .select(cols)
        .eq("status", "submitted")
        .order("updated_at", { ascending: false })
        .range(from, from + 999);
      if (error && cols !== LIST_COLUMNS) {
        cols = LIST_COLUMNS;
        from -= 1000;
        continue;
      }
      if (error) {
        setError(error.message);
        break;
      }
      out.push(...(data as unknown as Job[]));
      if (!data || data.length < 1000) {
        setError(null);
        break;
      }
    }
    setJobs(out);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 8000); // submissions are detected while a run is open
    return () => clearInterval(t);
  }, [load]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return needle ? jobs.filter((j) => `${j.company} ${j.role ?? ""}`.toLowerCase().includes(needle)) : jobs;
  }, [jobs, q]);

  const groups = useMemo(() => {
    const m = new Map<string, Job[]>();
    rows.forEach((j) => {
      const k = dayKey(j.updated_at);
      m.set(k, [...(m.get(k) ?? []), j]);
    });
    return [...m.values()];
  }, [rows]);

  const today = jobs.filter((j) => dayKey(j.updated_at) === new Date().toDateString()).length;
  const week = jobs.filter((j) => Date.now() - new Date(j.updated_at).getTime() < 7 * 86400000).length;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Applied</h1>
          <p className="muted">Everything you’ve submitted, newest first.</p>
        </div>
      </header>

      {error && <div className="banner error">Couldn’t load: {error}</div>}

      <section className="kpis three">
        <div className="kpi ok static">
          <div className="num">{today}</div>
          <div className="lbl">Applied today</div>
        </div>
        <div className="kpi static">
          <div className="num">{week}</div>
          <div className="lbl">Last 7 days</div>
        </div>
        <div className="kpi static">
          <div className="num">{jobs.length}</div>
          <div className="lbl">All time</div>
        </div>
      </section>

      <section className="toolbar">
        <div className="tools">
          <div className="search">
            <IconSearch />
            <input placeholder="Search company or role" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <button className="btn ghost icon-btn" onClick={load} title="Refresh">
            <IconRefresh />
          </button>
        </div>
      </section>

      {loading ? (
        <>
          <div className="skeleton row-skel" />
          <div className="skeleton row-skel" />
        </>
      ) : groups.length === 0 ? (
        <div className="empty">
          <div className="empty-art">✦</div>
          <b>{jobs.length ? "No matches" : "Nothing applied yet"}</b>
          <span className="muted">
            {jobs.length ? "Try a different search." : "When you submit an application it shows up here automatically."}
          </span>
        </div>
      ) : (
        groups.map((g) => (
          <section key={g[0].updated_at + g[0].id} className="day">
            <h3 className="day-head">
              {dayLabel(g[0].updated_at)} <span>{g.length}</span>
            </h3>
            <div className="list">
              {g.map((j) => (
                <JobItem key={j.id} j={j} selected={openId === j.id} onClick={() => setOpenId(j.id)} />
              ))}
            </div>
          </section>
        ))
      )}

      {openId !== null && (
        <Drawer
          id={openId}
          onClose={() => setOpenId(null)}
          onChanged={(p) => {
            // moved out of "submitted" (e.g. re-queued): it no longer belongs on this tab
            if (p.status && p.status !== "submitted") setJobs((js) => js.filter((j) => j.id !== openId));
          }}
        />
      )}
    </div>
  );
}
