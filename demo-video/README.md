# Demo video

`appli-demo.mp4` is a 62-second promo, 1920×1080 at 30fps. The dashboard screens in it use made-up data.

To re-render it you need Node 18+, ffmpeg, and the Inter Display and JetBrains Mono fonts.

```bash
cd dashboard && npm install && VITE_SUPABASE_URL=https://demo.supabase.co VITE_SUPABASE_ANON_KEY=demo npx vite build --outDir ../demo-video/dist
cd ../demo-video && npm install playwright && npx playwright install chromium
node shoot.js dist shots             # capture dashboard screens from mocked data (mock.js, studio.js)
node render.js stills . 4.6 28 45    # preview frames at these timestamps
node render.js video appli-demo.mp4 62.3
```

The scenes and timings are in `film.html`.
