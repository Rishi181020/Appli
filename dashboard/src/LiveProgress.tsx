import { useEffect, useState } from "react";
import { AgentStatus, Job, agent, skipExplanation, supabase } from "./lib";
import Drawer from "./Drawer";

const stages: Record<string, string> = {
  starting: "Preparing your batch", opening: "Opening job posting", screening: "Checking eligibility",
  matching: "Scanning and matching resumes", tailoring: "Drafting tailored resume", filling: "Filling application",
  answering: "Preparing answers", uploading: "Attaching documents", ready_for_review: "Application ready",
  needs_help: "Your answers are needed", resume_review: "Resume ready for approval", review: "Your review is next",
  queued: "Queued for another attempt", skipped: "Job skipped", failed: "Application needs attention",
  needs_manual: "Manual action needed",
  submitted: "Submission recorded",
};

/** Visible across tabs so background drafts and required actions never disappear with a filter. */
export default function LiveProgress({ ownerId, onResume, onApplications }: { ownerId: string; onResume: () => void; onApplications: () => void }) {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [offline, setOffline] = useState(false);
  const [skipped, setSkipped] = useState<Job[]>([]);
  const [openJob, setOpenJob] = useState<number | null>(null);
  const noticeKey = `appli.skipped-notices.${ownerId}`;
  const [dismissed, setDismissed] = useState<string[]>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(noticeKey) || "[]");
      return Array.isArray(saved) ? saved.filter(v => typeof v === "string") : [];
    } catch { return []; }
  });
  const dismiss = (job: Job) => {
    const next = [...dismissed, `${job.id}:${job.updated_at}`].slice(-200);
    setDismissed(next);
    try { localStorage.setItem(noticeKey, JSON.stringify(next)); } catch { /* still dismiss for this session */ }
  };
  useEffect(() => {
    let active = true;
    let polling = false;
    const loadActions = async () => {
      const { data, error } = await supabase.from("jobs")
        .select("id,company,status,resume_review")
        .or("status.in.(tailoring,needs_help,ready_for_review,submission_required),resume_review.eq.pending");
      if (error) throw error;
      return data as Job[];
    };
    const poll = async () => {
      if (polling) return;
      polling = true;
      const [run, list, skippedList] = await Promise.allSettled([
        agent<AgentStatus>("/api/status"), loadActions(),
        supabase.from("jobs").select("id,company,role,status,status_reason,url,updated_at")
          .eq("status", "skipped").neq("status_reason", "Skipped by you")
          .order("updated_at", { ascending: false }).limit(10),
      ]);
      polling = false;
      if (!active) return;
      setOffline(run.status === "rejected");
      if (run.status === "fulfilled") setStatus(run.value);
      if (list.status === "fulfilled") setJobs(list.value);
      if (skippedList.status === "fulfilled" && !skippedList.value.error) setSkipped(skippedList.value.data as Job[]);
    };
    poll();
    const timer = setInterval(poll, 3000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  const drafts = jobs.filter(j => j.status === "tailoring" && j.resume_review !== "pending");
  const pending = jobs.filter(j => j.resume_review === "pending");
  const forms = jobs.filter(j => ["needs_help", "ready_for_review", "submission_required"].includes(j.status));
  const current = status?.activity?.current;
  const events = status?.activity?.events ?? [];
  const notices = skipped.filter(j => !dismissed.includes(`${j.id}:${j.updated_at}`));
  if (!status?.running && !drafts.length && !pending.length && !forms.length && !events.length && !notices.length) return null;
  const working = status?.running && !offline && !status.stopping && current?.stage !== "review";
  return (
    <section className="live-progress panel" aria-label="Appli progress">
      <div className="live-progress-head" role="status" aria-live="polite">
        <div>
          <div className="eyebrow">{working && <span className="pulse" />}Appli activity</div>
          <h3>{offline ? "Progress connection lost" : status?.stopping ? "Stopping after the current step" :
            status?.running ? stages[current?.stage ?? "starting"] ?? "Working on your applications" : "Latest run activity"}</h3>
          {!offline && status?.running && current?.company && <b>{current.company} · {current.role}</b>}
          <p className="muted">{offline ? "Live updates are unavailable. Reconnect to the local app to see the latest progress." :
            status?.stopping ? "Appli is finishing its current step. Any remaining work will be available on the dashboard." :
            status?.running ? current?.message ?? "Loading your profile, saved answers and queued jobs…" :
            "Check the actions below to continue your applications."}</p>
        </div>
        {status?.running && status.progress.total > 0 && <span className="chip-tag">Application {status.progress.current || 1} / {status.progress.total}</span>}
      </div>
      <div className="live-progress-actions">
        {notices.length > 0 && <div className="skip-notices" aria-live="polite" aria-relevant="additions">
          <b>Jobs skipped after screening</b>
          {notices.map(j => <div className="skip-notice" key={`${j.id}:${j.updated_at}`}>
            <div><b>{j.company} · {j.role}</b><p>{skipExplanation(j.status_reason)}</p>
              {j.status_reason && <details><summary>Screening evidence</summary><p>{j.status_reason}</p></details>}
              <small>Check the posting and your profile if this looks incorrect.</small>
            </div>
            <div className="skip-notice-actions">
              <button className="btn small-btn" onClick={() => setOpenJob(j.id)}>Review job</button>
              {j.url && <a className="btn ghost small-btn" href={j.url} target="_blank" rel="noreferrer">Open posting</a>}
              <button className="btn ghost small-btn" onClick={() => dismiss(j)}>Dismiss notice</button>
            </div>
          </div>)}
        </div>}
        {drafts.length > 0 && <div className="progress-action"><span className="spinner" /><span><b>{drafts.length} resume draft{drafts.length === 1 ? "" : "s"} in progress</b><small>{drafts.map(j => j.company).slice(0, 3).join(", ")} · Applications wait for your approval.</small></span></div>}
        {pending.length > 0 && <div className="progress-action"><span><b>{pending.length} resume{pending.length === 1 ? "" : "s"} ready for review</b><small>Approve changes and build the resume, or choose your base resume.</small></span><button className="btn primary small-btn" onClick={onResume}>Review resumes</button></div>}
        {forms.length > 0 && <div className="progress-action"><span><b>{forms.length} application{forms.length === 1 ? "" : "s"} waiting for you</b><small>Answer flagged questions, review the browser tabs and submit each form yourself.</small></span><button className="btn small-btn" onClick={onApplications}>Open applications</button></div>}
      </div>
      {events.length > 0 && <details className="progress-history" open><summary>Recent updates</summary><ol>{[...events].reverse().map((e, i) => <li key={`${e.at}-${i}`}><time>{new Date(e.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time><span>{e.company && <b>{e.company}: </b>}{e.message}</span></li>)}</ol></details>}
      {openJob !== null && <Drawer id={openJob} onClose={() => setOpenJob(null)} onChanged={patch => setSkipped(rows => rows.map(j => j.id === openJob ? { ...j, ...patch } : j).filter(j => j.status === "skipped"))} />}
    </section>
  );
}
