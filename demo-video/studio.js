const bd = (pct, s, se, e, i) => ({ pct, band: pct > 0.85 ? "strong" : pct > 0.7 ? "good" : "weak", parts: { skills: s, seniority: se, education: e, industry: i }, reasons: [], yoe: 1 });
const match = { terms: [], scores: { backend: { pct: 0.68 }, fullstack: { pct: 0.61 } }, chosen: "backend", labels: { backend: "Backend", fullstack: "Full-stack" }, pct: 0.68, exact_pct: 0.62, missing: ["Kafka", "gRPC"], tailorable: true };
const kw = (term, group, required = false) => ({ term, category: "skill", required, group, declined: false });
const base = {
  draft_id: "d1", job: { company: "Quartz Pay", role: "Software Engineer, Payments" }, match,
  edits: { base: "backend", pct_before: 0.68, pct_after: 0.9, threshold: 0.8, edits: [], gaps: [], problems: [], format: "tex" },
  restriction: null,
  keywords: [kw("Go", "have", true), kw("PostgreSQL", "have", true), kw("Distributed systems", "have"), kw("Event-driven architecture", "similar"), kw("Kafka", "similar", true), kw("gRPC", "missing")],
  targets: ["Go", "PostgreSQL", "Distributed systems", "Kafka"], mode: null,
  score_base: bd(0.68, 0.62, 0.9, 1, 0.5), score_after: null, bank_ready: true, chat: [], reviews: [], file: null, versions: [], style: {}, prompt: "",
};
const B = (bid, path, text, b) => ({ bid, path, text, base: b, status: "pending", source: "bank", can_revert: true });
const generated = {
  ...base, mode: "bank", score_after: bd(0.92, 0.94, 0.9, 1, 0.8), file: "Alex Resume Quartz Pay.pdf",
  versions: [{ id: "v1", at: "Today 10:42", files: ["Alex Resume Quartz Pay.pdf"], pct: 0.92, note: "Claude, one pass" }],
  doc: { pages: 1, warnings: [], skills: [{ group: "Languages", items: "Go, Python, TypeScript, SQL" }, { group: "Systems", items: "PostgreSQL, Kafka, Redis, Docker, AWS" }],
    sections: [{ type: "experience", heading: "Experience", entries: [
      { id: "e1", title: "Software Engineer Intern", org: "Ledgerline", start: "Jun 2026", end: "Sep 2026", bullets: [
        B("b1", "Experience › Ledgerline › 1", "Built an event-driven **Kafka** pipeline in **Go** that settled 2M+ card transactions a day with exactly-once writes to **PostgreSQL**", "Built a pipeline in Python that processed card transactions and wrote them to the database"),
        B("b2", "Experience › Ledgerline › 2", "Cut p99 payment-authorization latency 38% by moving hot reads to Redis and batching ledger writes", "Improved API performance by adding caching"),
      ] },
      { id: "e2", title: "Research Assistant, Distributed Systems Lab", org: "State University", start: "Jan 2025", end: "May 2026", bullets: [
        B("b3", "Experience › Lab › 1", "Implemented a Raft-based replicated key-value store in **Go**; survived 1,000 chaos-test partitions with zero lost writes", "Worked on a distributed key-value store project"),
      ] },
    ] }] },
};
module.exports = (p) => (p.endsWith("/generate") ? generated : base);
