// Mock Supabase (auth + PostgREST) and the local agent API for a demo recording.
const now = Date.now();
const ago = (m) => new Date(now - m * 60000).toISOString();
const USER = { id: "u-demo", aud: "authenticated", role: "authenticated", email: "alex@example.com", app_metadata: {}, user_metadata: {}, created_at: ago(9999) };
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
const JWT = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER.id, exp: 4102444800, role: "authenticated", email: USER.email })}.sig`;
const SESSION = { access_token: JWT, refresh_token: "r", token_type: "bearer", expires_in: 999999, expires_at: 4102444800, user: USER };

const filled = {
  fields: [
    { label: "First name", value: "Alex", tier: "profile", confidence: 1 },
    { label: "Last name", value: "Rivera", tier: "profile", confidence: 1 },
    { label: "Email", value: "alex@example.com", tier: "profile", confidence: 1 },
    { label: "LinkedIn", value: "linkedin.com/in/alex-rivera", tier: "profile", confidence: 1 },
    { label: "Will you require visa sponsorship?", value: "Yes", tier: "saved", confidence: 1 },
    { label: "Why do you want to work here?", value: "Your payments infra team ships the kind of reliability work I did at…", tier: "llm", confidence: 0.86 },
    { label: "Earliest start date", value: "June 2027", tier: "saved", confidence: 1 },
  ],
  uploads: ["Alex Resume Backend.pdf", "Cover letter.pdf"],
};
const J = (id, company, role, status, extra = {}) => ({
  id, company, role, location: extra.location ?? "San Francisco, CA", salary: null, url: "https://example.com/jobs/" + id,
  source: "Find jobs: company boards", fit_tier: extra.tier ?? 1, days_posted: extra.days ?? 1, ats: extra.ats ?? "greenhouse",
  status, status_reason: extra.reason ?? null, resume_used: extra.resume ?? "backend", cover_letter: null, screenshot_path: null,
  filled_fields: status === "ready_for_review" || status === "submitted" ? filled : null, unanswered: extra.unanswered ?? [],
  attempts: 1, updated_at: ago(extra.min ?? 30), created_at: ago(60 * 24), match_pct: extra.match ?? 0.86,
  resume_review: extra.review ?? null, submitted_at: status === "submitted" ? ago(extra.min ?? 300) : null,
  resume_match: { terms: [], scores: { backend: { pct: extra.match ?? 0.86 }, fullstack: { pct: 0.71 } }, chosen: "backend", chosen_by: "score", labels: { backend: "Backend", fullstack: "Full-stack" }, pct: extra.match ?? 0.86, missing: ["Kafka"], exact_pct: 0.78 },
});
const JOBS = [
  J(1, "Northwind Labs", "Software Engineer, New Grad", "ready_for_review", { match: 0.91, min: 4 }),
  J(2, "Helio Robotics", "Backend Engineer I", "ready_for_review", { match: 0.88, min: 9, ats: "lever" }),
  J(3, "Quartz Pay", "Software Engineer, Payments", "filling", { match: 0.84, min: 1, ats: "ashby" }),
  J(4, "Lumen Health", "Full-stack Engineer", "needs_help", { match: 0.8, min: 14, unanswered: [{ id: "q1", label: "Portfolio URL", reason: "Not in your profile", required: true }] }),
  J(5, "Orbitly", "Platform Engineer, New Grad", "queued", { match: 0.83, ats: "workday", min: 2 }),
  J(6, "Cobalt Data", "Data Engineer I", "queued", { match: 0.79, min: 2, tier: 2 }),
  J(7, "Fernway", "Software Engineer I", "queued", { match: 0.77, min: 2, tier: 2, ats: "lever" }),
  J(8, "Arcadia Games", "Gameplay Engineer", "skipped", { reason: "No visa sponsorship: \"we are unable to sponsor\"", min: 40, tier: 3 }),
  J(9, "Sentinel Defense", "Software Engineer", "skipped", { reason: "Security clearance required", min: 41, tier: 3 }),
  ...["Pinecrest AI", "Tidal Systems", "Brightline", "Mosaic Cloud", "Ember Labs", "Vantage Bio", "Kestrel", "Nimbus Works", "Atlas Freight", "Juniper Ops", "Redwood Analytics", "Polar Signal"].map((c, i) =>
    J(20 + i, c, ["Software Engineer, New Grad", "Backend Engineer", "SWE I", "Platform Engineer"][i % 4], "submitted", { min: 120 + i * 90, match: 0.8 + (i % 5) * 0.03 })),
];

function parseIn(v) { return v.replace(/^\(|\)$/g, "").split(",").map((s) => s.replace(/^"|"$/g, "")); }
function test(row, col, expr) {
  const [op, ...rest] = expr.split("."); const v = rest.join(".");
  const x = row[col] == null ? null : String(row[col]);
  if (op === "eq") return x === v; if (op === "neq") return x !== v;
  if (op === "in") return parseIn(v).includes(x); if (op === "is") return v === "null" ? x === null : true;
  if (op === "not") return !test(row, col, v);
  return true;
}
function splitTop(s) { const out = []; let d = 0, cur = ""; for (const ch of s) { if (ch === "(") d++; if (ch === ")") d--; if (ch === "," && d === 0) { out.push(cur); cur = ""; } else cur += ch; } if (cur) out.push(cur); return out; }
function orTest(row, v) { return splitTop(v.replace(/^\(|\)$/g, "")).some((c) => { const [col, ...r] = c.split("."); return test(row, col, r.join(".")); }); }

async function rest(route, req, state) {
  const u = new URL(req.url()); const table = u.pathname.split("/").pop();
  const method = req.method();
  const accept = req.headers()["accept"] || "";
  if (table === "profiles") return route.fulfill({ json: accept.includes("object") ? { data: { name: "Alex Rivera" }, markdown: "" } : [] });
  if (table === "answers") return route.fulfill({ json: [
    { id: 1, question_key: "sponsorship", question_text: "Will you now or in the future require sponsorship?", answer: "Yes", source: "manual", uses: 31 },
    { id: 2, question_key: "relocate", question_text: "Are you willing to relocate?", answer: "Yes", source: "learned", uses: 18 },
    { id: 3, question_key: "start", question_text: "Earliest start date", answer: "June 2027", source: "manual", uses: 24 },
  ] });
  if (table !== "jobs") return route.fulfill({ json: [] });
  if (method === "PATCH") return route.fulfill({ status: 204, body: "" });
  let rows = state.jobs.filter((r) => [...u.searchParams].every(([k, v]) => k === "select" || k === "order" || k === "limit" || k === "offset" ? true : k === "or" ? orTest(r, v) : test(r, k, v)));
  const lim = u.searchParams.get("limit"); if (lim) rows = rows.slice(0, +lim);
  const headers = { "content-range": `0-${Math.max(rows.length - 1, 0)}/${rows.length}`, "access-control-expose-headers": "content-range" };
  if (method === "HEAD") return route.fulfill({ status: 200, headers, body: "" });
  if (accept.includes("vnd.pgrst.object")) return route.fulfill({ json: rows[0] ?? null, headers });
  return route.fulfill({ json: rows, headers });
}

const ME = { email: USER.email, folder: "users/Alex Rivera", name: "Alex Rivera", resumes: [{ key: "backend", label: "Backend", pdf: "a.pdf", pdf_ok: true, tex: true, source: "tex", focus: "" }],
  cover_letter: "cover.docx", profile_md: true, has_profile: true, db_ready: true, runner_connected: true, legacy_found: null,
  keys: { own_key: true, hint: "sk-or-…4f2a", env_key: false, models: { fast: "x", write: "y", jev: "z" } } };

async function api(route, req, state) {
  const p = new URL(req.url()).pathname;
  const ok = (json) => route.fulfill({ json });
  if (p === "/api/me") return ok(ME);
  if (p === "/api/status") return ok(state.status());
  if (p === "/api/screen") return ok({ running: false, current: 0, total: 0, skipped: 0, done: "" });
  if (p === "/api/resume-studio/drafts") return ok({ drafts: [], jobs: [] });
  if (p.startsWith("/api/resume-studio/")) { if (state.studioDelay) await new Promise((r) => setTimeout(r, state.studioDelay)); return ok(state.studio(p)); }
  if (p === "/api/find") return ok({ status: { running: false, current: 0, total: 0, lines: [], exit: 0 }, prefs: {}, found: { results: [] } });
  return ok({});
}

module.exports = { SESSION, JOBS, api, rest };
