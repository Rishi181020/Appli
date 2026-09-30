import { useState } from "react";
import type { AgentStatus } from "./lib";
import { agent, matchTone, pctText, resumeLabel } from "./lib";
import { IconAlert, IconPlay, IconStop, IconTerminal } from "./Icons";

const GOAL_KEY = "appli.goal";
export const getGoal = () => {
  try {
    return Number(localStorage.getItem(GOAL_KEY)) || 20;
  } catch {
    return 20;
  }
};

function Ring({ value, goal }: { value: number; goal: number }) {
  const r = 34;
  const c = 2 * Math.PI * r;
  const pct = Math.min(1, value / Math.max(1, goal));
  return (
    <div className="ring" title={`${value} of ${goal} submitted today`}>
      <svg viewBox="0 0 84 84" width="84" height="84">
        <defs>
          <linearGradient id="rg" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="var(--accent)" />
            <stop offset="1" stopColor="var(--accent2)" />
          </linearGradient>
        </defs>
        <circle cx="42" cy="42" r={r} className="ring-bg" />
        <circle cx="42" cy="42" r={r} className="ring-fg" strokeDasharray={`${c * pct} ${c}`} transform="rotate(-90 42 42)" />
      </svg>
      <div className="ring-label">
        <b>{value}</b>
        <span>/ {goal}</span>
      </div>
    </div>
  );
}

type Props = {
  status: AgentStatus | null;
  offline: boolean;
  queued: number;
  ready: number;
  submittedToday: number;
  onChanged: () => void;
};

export default function RunCard({ status, offline, queued, ready, submittedToday, onChanged }: Props) {
  const [limit, setLimit] = useState(getGoal());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);

  const running = !!status?.running;
  const p = status?.progress;
  const filling = running && p && p.current < p.total;

  async function run() {
    setBusy(true);
    setError(null);
    try {
      try {
        localStorage.setItem(GOAL_KEY, String(limit));
      } catch {
        /* storage unavailable: fine */
      }
      await agent("/api/run", { limit });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }

  async function stop() {
    setBusy(true);
    try {
      await agent("/api/stop", {});
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }

  // The runner logs "resume: swe (74% match, chosen by Jev 0.97; ...)" for the job it is on; show that live.
  const current = (() => {
    const log = status?.log ?? [];
    const start = log.map((l) => /^\[\d+\/\d+\]/.test(l)).lastIndexOf(true);
    if (start < 0) return null;
    const line = log.slice(start).find((l) => l.trim().startsWith("resume:"));
    const m = line?.match(/resume: (\S+) \((\d+)% match(?:, chosen by ([^;)]+))?/);
    if (!m) return null;
    return { resume: resumeLabel(m[1]), pct: Number(m[2]) / 100, by: m[3] ?? "", tailoring: /-> tailoring/.test(line ?? "") };
  })();

  let title = "Ready when you are";
  let sub = `${queued} in the queue${ready ? ` · ${ready} waiting for your review` : ""}`;
  if (offline) {
    title = "Agent not connected";
    sub = "Open the app with start.bat so the Run button can control your browser.";
  } else if (status?.stopping) {
    title = "Stopping…";
    sub = "Finishing the current step, then closing.";
  } else if (filling && p) {
    title = `Filling application ${p.current} of ${p.total}`;
    sub = p.label;
  } else if (running) {
    title = "Review tabs are open";
    sub = "Submit each one yourself. Submissions are detected automatically.";
  }

  return (
    <section className={`hero ${running ? "live" : ""}`}>
      <Ring value={submittedToday} goal={getGoal()} />
      <div className="hero-main">
        <div className="eyebrow">{running ? <span className="pulse" /> : null}Daily batch</div>
        <h2>{title}</h2>
        <p className="muted">{sub}</p>
        {filling && current && (
          <div className="live-match">
            <span className={`matchpill ${matchTone(current.pct)}`}>{pctText(current.pct)}</span>
            <span className="muted small">
              {current.resume} resume{current.by ? ` · chosen by ${current.by}` : ""}
              {current.tailoring ? " · below target: pausing to tailor" : ""}
            </span>
          </div>
        )}
        {filling && p && (
          <div className="bar" aria-label="progress">
            <div style={{ width: `${(p.current / Math.max(1, p.total)) * 100}%` }} />
          </div>
        )}
        {error && (
          <div className="inline-error">
            <IconAlert /> {error}
          </div>
        )}
      </div>
      <div className="hero-actions">
        {!running ? (
          <>
            <label className="limit">
              <span>Applications</span>
              <input type="number" min={1} max={100} value={limit} onChange={(e) => setLimit(Math.max(1, Number(e.target.value) || 1))} />
            </label>
            <button className="btn primary big" disabled={busy || offline || queued === 0} onClick={run}>
              <IconPlay /> Run {limit}
            </button>
          </>
        ) : (
          <button className="btn danger big" disabled={busy || status?.stopping} onClick={stop}>
            <IconStop /> {status?.stopping ? "Stopping…" : "Stop"}
          </button>
        )}
        <button className="btn ghost small-btn" onClick={() => setShowLog((v) => !v)}>
          <IconTerminal /> {showLog ? "Hide" : "Activity"}
        </button>
      </div>
      {showLog && (
        <pre className="log">{(status?.log?.length ? status.log : ["No activity yet."]).join("\n")}</pre>
      )}
    </section>
  );
}
