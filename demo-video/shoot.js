// Captures real dashboard screens (served from the built bundle) with mocked data.
const { chromium } = require("playwright");
const http = require("http"), fs = require("fs"), path = require("path");
const { SESSION, JOBS, api, rest } = require("./mock");
const studio = require("./studio");
const DIST = process.argv[2], OUT = process.argv[3];
fs.mkdirSync(OUT, { recursive: true });
const types = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml" };
const srv = http.createServer((q, r) => { let f = path.join(DIST, q.url.split("?")[0]); if (!fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(DIST, "index.html"); r.writeHead(200, { "content-type": types[path.extname(f)] || "application/octet-stream" }); r.end(fs.readFileSync(f)); }).listen(4321);

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  const state = { jobs: JOBS, studio, studioDelay: 0, status: () => ({ running: true, stopping: false, progress: { current: 7, total: 20, label: "Quartz Pay: filling the form" }, log: [],
    activity: { current: { stage: "fill", message: "Quartz Pay · Software Engineer, Payments: answering 14 questions", at: new Date().toISOString(), background: false, job_id: 3, company: "Quartz Pay", role: "Software Engineer, Payments" }, events: [] } }) };
  await ctx.route("https://demo.supabase.co/**", (route, req) => {
    const u = new URL(req.url());
    if (req.method() === "OPTIONS") return route.fulfill({ status: 200, headers: { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" } });
    if (u.pathname.startsWith("/auth/v1/user")) return route.fulfill({ json: SESSION.user });
    if (u.pathname.startsWith("/auth/v1/")) return route.fulfill({ json: SESSION });
    if (u.pathname.startsWith("/rest/v1/")) return rest(route, req, state);
    return route.fulfill({ json: {} });
  });
  await ctx.route("http://localhost:4321/api/**", (route, req) => api(route, req, state));
  await ctx.addInitScript((s) => { localStorage.setItem("sb-demo-auth-token", JSON.stringify(s)); localStorage.setItem("appli-theme", "dark"); }, SESSION);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log("PAGEERR", e.message));
  page.on("console", (m) => m.type() === "error" && console.log("CONSOLE", m.text()));
  await page.goto("http://localhost:4321/");
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}/jobs.png` });
  await page.locator(".live-progress").first().screenshot({ path: `${OUT}/el-live.png` }).catch(e=>console.log("live",e.message));
  await page.locator(".list").first().scrollIntoViewIfNeeded(); await page.waitForTimeout(300);
  await page.locator(".list").first().screenshot({ path: `${OUT}/el-list.png` }).catch(e=>console.log("list",e.message));
  // open a ready-for-review job
  const item = page.locator("button.item", { hasText: "Northwind Labs" }).first();
  if (await item.count()) { await item.click(); await page.waitForTimeout(1200); await page.screenshot({ path: `${OUT}/drawer.png` }); await page.locator("aside.drawer").screenshot({ path: `${OUT}/el-drawer.png` }); await page.keyboard.press("Escape"); }
  await page.getByRole("button", { name: /Tailor resume/ }).click();
  await page.waitForTimeout(500);
  await page.locator("input[placeholder='Company name']").fill("Quartz Pay");
  await page.locator("input[placeholder='Software Engineer']").fill("Software Engineer, Payments");
  await page.locator("textarea").first().fill("We're hiring a Software Engineer to build the payments platform that moves money for millions of businesses. You'll design event-driven services in Go, own PostgreSQL schemas, and work with Kafka to process card transactions with exactly-once guarantees. Requirements: BS in CS or related field, strong fundamentals in distributed systems, experience with Go, PostgreSQL and Kafka, gRPC a plus. You'll partner with risk, ledger and infra teams.");
  await page.screenshot({ path: `${OUT}/studio-input.png` });
  await page.getByRole("button", { name: "Analyze" }).click();
  await page.waitForTimeout(1200);
  await page.evaluate(() => window.scrollTo(0, 0));
  const res = page.locator(".studio-result").first(); await res.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${OUT}/studio-analyze.png` }); await page.locator(".studio-result").nth(1).screenshot({ path: `${OUT}/el-keywords.png` }).catch(e=>console.log("kw",e.message));
  state.studioDelay = 4000;
  await page.getByRole("button", { name: /Generate tailored resume/ }).click();
  await page.waitForTimeout(600);
  await page.locator(".banner[role=status]").scrollIntoViewIfNeeded().catch(() => {});
  await page.screenshot({ path: `${OUT}/studio-generating.png` });
  await page.waitForTimeout(4500);
  await page.getByText("Your tailored resume").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${OUT}/studio-result.png` }); await page.locator(".score-card").last().screenshot({ path: `${OUT}/el-score.png` });
  await page.locator("label.check", { hasText: "Side by side" }).locator("input").check();
  await page.waitForTimeout(300);
  await page.getByRole("heading", { name: "Changes", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${OUT}/studio-diff.png` }); await page.locator(".studio-section").first().screenshot({ path: `${OUT}/el-diff.png` });
  await page.getByRole("button", { name: /Applied/ }).click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/applied.png` });
  await browser.close(); srv.close();
})();
