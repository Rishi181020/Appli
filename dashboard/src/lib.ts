import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string;
const key = import.meta.env.VITE_SUPABASE_ANON_KEY as string;
export const configured = Boolean(url && key);
// createClient throws on empty values, so fall back to placeholders and let App show a setup message.
export const supabase = createClient(url || "http://localhost", key || "missing");

export type Status =
  | "queued"
  | "filling"
  | "tailoring"
  | "needs_help"
  | "submission_required"
  | "ready_for_review"
  | "submitted"
  | "skipped"
  | "needs_manual"
  | "failed";

export const STATUSES: Status[] = [
  "needs_help",
  "submission_required",
  "ready_for_review",
  "tailoring",
  "queued",
  "filling",
  "submitted",
  "needs_manual",
  "skipped",
  "failed",
];

export const STATUS_LABEL: Record<Status, string> = {
  queued: "Queued",
  filling: "Filling",
  tailoring: "Tailoring resume",
  needs_help: "Needs your help",
  submission_required: "Submission required",
  ready_for_review: "Ready for review",
  submitted: "Submitted",
  skipped: "Skipped",
  needs_manual: "Needs manual",
  failed: "Failed",
};

export type Unanswered = {
  id: string;
  label: string;
  reason: string;
  required?: boolean;
  options?: string[];
};

export type Job = {
  id: number;
  company: string;
  role: string | null;
  location: string | null;
  salary: string | null;
  url: string | null;
  source: string | null;
  fit_tier: number | null;
  days_posted: number | null;
  ats: string | null;
  status: Status;
  status_reason: string | null;
  resume_used: string | null;
  cover_letter: string | null;
  screenshot_path: string | null;
  filled_fields: {
    fields: { label: string; value: string; tier: string; confidence?: number }[];
    uploads: string[];
    captcha?: boolean;
  } | null;
  unanswered: Unanswered[] | null;
  attempts: number;
  updated_at: string;
  resume_match?: ResumeMatch | null;
  resume_review?: "pending" | "built" | "dismissed" | null;
  resume_file?: string | null;
  match_pct?: number | null;
  submitted_at?: string | null; // when it was submitted (migration 007); older rows fall back to updated_at
};

export type Answer = {
  id: number;
  question_key: string;
  question_text: string | null;
  answer: string;
  source?: "manual" | "learned";
  options?: string[] | null;
  uses?: number;
};

/** Save an answer as the signed-in person's own (overrides a learned one). Answers are unique per person. */
export async function saveAnswer(question_key: string, question_text: string, answer: string) {
  const { data } = await supabase.auth.getUser();
  const row = { owner_id: data.user?.id, question_key, question_text, answer, source: "manual" };
  const first = await supabase.from("answers").upsert(row, { onConflict: "owner_id,question_key" });
  if (!first.error) return first;
  // before migration 004 (single-user schema)
  const { owner_id: _o, ...legacy } = row;
  return supabase.from("answers").upsert(legacy, { onConflict: "question_key" });
}

/** A resume's display name: the person's own label (stored with each match), else the key. */
export function resumeLabel(key: string, labels?: Record<string, string> | null): string {
  return labels?.[key] ?? key.toUpperCase();
}

/** Match % helpers: green at or above the 80% target, amber from 60%, red below. */
export const MATCH_TARGET = 0.8;
export const matchTone = (p: number | null | undefined) =>
  p == null ? "none" : p >= MATCH_TARGET ? "good" : p >= 0.6 ? "mid" : "low";
export const pctText = (p: number | null | undefined) => (p == null ? "–" : `${Math.round(p * 100)}%`);

export type AgentStatus = {
  running: boolean;
  stopping: boolean;
  progress: { current: number; total: number; label: string };
  log: string[];
};

/** Must match runner/answering.py norm(): lowercase, collapse spaces, strip non [a-z0-9 ]. */
export const norm = (s: string) => s.toLowerCase().replace(/\s+/g, " ").replace(/[^a-z0-9 ]/g, "").trim();

/** PostgREST returns at most 1000 rows per request, so page through. */
export async function fetchAllJobs(): Promise<Job[]> {
  // match_pct / resume_review arrive with migrations 002 + 003, submitted_at with 007; fall back gracefully until run
  for (const cols of [`${LIST_COLUMNS},match_pct,resume_review,submitted_at`, `${LIST_COLUMNS},match_pct,resume_review`, LIST_COLUMNS]) {
    const out: Job[] = [];
    let failed = false;
    for (let from = 0; ; from += 1000) {
      const { data, error } = await supabase
        .from("jobs")
        .select(cols)
        .order("fit_tier", { ascending: true })
        .order("days_posted", { ascending: true })
        .range(from, from + 999);
      if (error) {
        if (cols === LIST_COLUMNS) throw error;
        failed = true;
        break;
      }
      out.push(...(data as unknown as Job[]));
      if (!data || data.length < 1000) break;
    }
    if (!failed) return out;
  }
  return [];
}

/** Talks to the local agent server (runner/server.py). */
// The latest access token, for links that open in a new tab (they can't send headers).
let currentToken = "";
supabase.auth.onAuthStateChange((_e, s) => {
  currentToken = s?.access_token ?? "";
});

/** Call the local Appli server (start.bat) as the signed-in person. */
export async function agent<T = unknown>(path: string, body?: unknown): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token ?? "";
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", "X-Appli": "1", Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((json as { error?: string }).error ?? `HTTP ${res.status}`);
  return json as T;
}

/** A tailored resume PDF from this computer (opens in a new tab). */
export const tailoredPdfUrl = (jobId: number) => `/api/resume/${jobId}.pdf?token=${encodeURIComponent(currentToken)}`;

/**
 * Give the runner its own sign-in: a second, separate session (never stored in the browser), handed to the local
 * server. The dashboard and the runner then never use up each other's refresh tokens, and no password is saved.
 */
export async function connectRunner(email: string, password: string) {
  const runner = createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false, storageKey: "appli-runner" },
  });
  const { data, error } = await runner.auth.signInWithPassword({ email, password });
  if (error || !data.session) throw new Error(error?.message ?? "Sign-in failed");
  await agent("/api/session", { access_token: data.session.access_token, refresh_token: data.session.refresh_token });
}

/** What this computer has for the signed-in person (from the local server). */
export type ResumeFile = { key: string; label: string; pdf: string; pdf_ok: boolean; tex: boolean; focus: string };
export type Me = {
  email: string;
  folder: string | null;
  name?: string;
  resumes: ResumeFile[];
  cover_letter: string | null;
  profile_md: boolean;
  has_profile: boolean;
  db_ready: boolean;
  runner_connected: boolean;
  legacy_found: { profile: string; resumes: string[]; cover_letter: boolean } | null;
  workday?: { email: string; has_password: boolean };
  keys?: { own_key: boolean; hint: string; env_key: boolean; models: { fast: string; write: string; jev: string } };
};

/** A picked file as base64 (the local server writes it into your folder). */
export function fileToBase64(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] ?? "");
    r.onerror = () => reject(new Error(`Couldn't read ${f.name}`));
    r.readAsDataURL(f);
  });
}

export function timeAgo(iso: string): string {
  const s = Math.max(1, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function hue(name: string): number {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}

/** Everything except submitted jobs: those live on the Applied tab. */
export const ACTIVE_STATUSES: Status[] = STATUSES.filter((s) => s !== "submitted");

export const LIST_COLUMNS =
  "id,company,role,location,url,fit_tier,days_posted,ats,status,status_reason,resume_used,attempts,updated_at,unanswered";

// ---- resume tailoring ----
export type ResumeScore = { pct: number; matched: string[]; missing: string[] };
export type ResumeMatch = {
  terms: { term: string; aliases: string[]; required: boolean }[];
  scores: Record<string, ResumeScore>;
  chosen: string;
  chosen_by?: "jev" | "score";
  jev_confidence?: number;
  labels?: Record<string, string>;
  tailorable?: boolean;
  exact_pct?: number;
  equivalent?: string[];
  skills_only?: string[];
  stuffed?: string[];
  pct: number;
  missing: string[];
};
export type ResumeEdit = {
  id: string;
  unit_id: string;
  kind: string;
  context: string;
  before: string;
  after: string;
  keywords_added: string[];
  evidence: string;
  added_words: string[];
  removed_words: string[];
  // skills-only tailoring
  skill?: string;
  category?: string;
  required?: boolean;
  support?: "backed" | "shown_as" | "not_found";
  default?: boolean;
};
export type ResumeProblem = {
  kind: string;
  detail?: string;
  unit_id?: string;
  context?: string;
  proposed?: string;
  reasons?: string[];
  terms?: string[];
};
export type ResumeEdits = {
  mode?: "skills";
  base: string;
  pct_before: number;
  pct_after: number;
  pct_if_all?: number;
  threshold: number;
  edits: ResumeEdit[];
  gaps: string[];
  problems: ResumeProblem[];
  built?: { pct_after: number | null; added_words: string[]; removed_words: string[]; accepted: string[]; file: string };
};
export type ResumeJob = Pick<Job, "id" | "company" | "role" | "ats" | "status" | "updated_at"> & {
  resume_match: ResumeMatch | null;
  resume_edits: ResumeEdits | null;
  resume_review: "pending" | "built" | "dismissed" | null;
  resume_file: string | null;
};

export type BuildResult = {
  ok: boolean;
  error?: string;
  file?: string;
  pct_after?: number | null;
  added_words?: string[];
  removed_words?: string[];
  db_warning?: string;
  next?: string;
  queued?: boolean; // back in the queue to be filled with the tailored resume
};



/** Word-level diff (LCS) so an edit can be shown with additions highlighted and removals struck through. */
export function diffWords(before: string, after: string): { a: { t: string; add: boolean }[]; b: { t: string; del: boolean }[] } {
  const key = (w: string) => w.toLowerCase().replace(/[.,;:]+$/, "");
  const x = before.split(/\s+/).filter(Boolean);
  const y = after.split(/\s+/).filter(Boolean);
  const n = x.length;
  const m = y.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = key(x[i]) === key(y[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const keepX = new Set<number>();
  const keepY = new Set<number>();
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (key(x[i]) === key(y[j])) {
      keepX.add(i++);
      keepY.add(j++);
    } else if (dp[i + 1][j] >= dp[i][j + 1]) i++;
    else j++;
  }
  return {
    b: x.map((t, k) => ({ t, del: !keepX.has(k) })),
    a: y.map((t, k) => ({ t, add: !keepY.has(k) })),
  };
}
