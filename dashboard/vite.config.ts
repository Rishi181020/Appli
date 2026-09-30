import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// Reads the project-root .env. Only the URL and the *publishable* key are exposed to the browser;
// SUPABASE_SERVICE_KEY and every other secret stay out of the bundle.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "");
  return {
    plugins: [react()],
    // during `npm run dev`, forward the Run/Stop API to the local agent server
    server: { proxy: { "/api": "http://127.0.0.1:8765" } },
    define: {
      "import.meta.env.VITE_SUPABASE_URL": JSON.stringify(env.VITE_SUPABASE_URL || env.SUPABASE_URL || ""),
      "import.meta.env.VITE_SUPABASE_ANON_KEY": JSON.stringify(
        env.VITE_SUPABASE_ANON_KEY || env.SUPABASE_PUBLISHABLE_KEY || env.SUPABASE_ANON_KEY || ""
      ),
    },
  };
});
