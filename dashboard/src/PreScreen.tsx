import { useCallback, useEffect, useState } from "react";
import { agent } from "./lib";

type ScreenStatus = { running: boolean; current: number; total: number; skipped: number; done: string };

/**
 * Reads every queued Workday posting (no form, no account) and skips the ones you can't take: no sponsorship,
 * US citizens only, clearance, closed. Patterns first, then Jev. Runs beside a normal run.
 */
export default function PreScreen({ onDone }: { onDone: () => void }) {
  const [st, setSt] = useState<ScreenStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const poll = useCallback(async () => {
    try {
      setSt(await agent<ScreenStatus>("/api/screen"));
    } catch {
      /* server offline: the Run card already says so */
    }
  }, []);

  useEffect(() => {
    poll();
  }, [poll]);

  const running = !!st?.running;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(poll, 3000);
    return () => {
      clearInterval(t);
      onDone(); // refresh the list once it finishes
    };
  }, [running, poll, onDone]);

  async function start() {
    setError(null);
    try {
      await agent("/api/screen", { ats: "workday" });
      poll();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="prescreen">
      <button
        className="btn"
        disabled={running}
        onClick={start}
        title="Reads each queued Workday posting and skips the ones that rule out sponsorship, require US citizenship or a clearance, or are closed. No forms are opened."
      >
        {running ? `Screening Workday ${st!.current}/${st!.total || "…"}` : "Pre-screen Workday jobs"}
      </button>
      {running && st!.skipped > 0 && <span className="small muted">{st!.skipped} skipped so far</span>}
      {!running && st?.done && <span className="small muted">{st.done.replace(/^Done: /, "Last pre-screen: ")}</span>}
      {error && <span className="small bad-text">{error}</span>}
    </div>
  );
}
