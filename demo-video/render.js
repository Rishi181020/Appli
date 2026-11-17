const { chromium } = require("playwright");
const { spawn } = require("child_process");
const path = require("path");
const [mode, out, ...times] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  p.on("pageerror", (e) => console.log("ERR", e.message));
  await p.goto("file://" + path.resolve("film.html"));
  await p.evaluate(() => document.fonts.ready);
  await p.waitForTimeout(500);
  if (mode === "stills") {
    for (const t of times) { await p.evaluate((t) => seek(t), +t); await p.screenshot({ path: `${out}/t${t}.png` }); }
  } else {
    const FPS = 30, DUR = +times[0];
    const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
      "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out], { stdio: ["pipe", "inherit", "inherit"] });
    for (let i = 0; i < FPS * DUR; i++) {
      await p.evaluate((t) => seek(t), i / FPS);
      const buf = await p.screenshot({ type: "jpeg", quality: 95 });
      if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once("drain", r));
      if (i % 300 === 0) console.log("frame", i);
    }
    ff.stdin.end(); await new Promise((r) => ff.on("close", r));
  }
  await b.close();
})();
