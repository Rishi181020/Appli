import { useState } from "react";
import { Me, agent } from "./lib";
import { ApiKeyManager, CoverManager, ResumeManager } from "./Files";
import ProfileView from "./ProfileView";
import AddJobs from "./AddJobs";
import { IconAlert, IconCheck } from "./Icons";

const STEPS = ["Welcome", "API key", "Resumes", "Cover letter", "Profile", "Jobs", "Done"] as const;

type FolderStatus = Pick<Me, "folder" | "resumes" | "cover_letter" | "profile_md">;

/** First sign-in on this computer: everything the app needs, once. */
export default function Onboarding({ me, setMe, onFinish }: { me: Me; setMe: (m: Me) => void; onFinish: () => void }) {
  const keyReady = Boolean(me.keys?.own_key || me.keys?.env_key);
  const first = !me.folder ? 0 : !keyReady ? 1 : !me.resumes.length ? 2 : !me.has_profile ? 4 : 5;
  const [step, setStep] = useState<number>(first);
  const [name, setName] = useState(me.name ?? "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [jobsAdded, setJobsAdded] = useState(0);

  const merge = (s: Partial<Me>) => setMe({ ...me, ...s });
  const canGo = (i: number) =>
    i === 0 || (Boolean(me.folder) && (i === 1 || (keyReady && (i === 2 || (me.resumes.length > 0 && (i <= 4 || me.has_profile))))));

  async function start() {
    setBusy(true);
    setMsg(null);
    try {
      merge(await agent<FolderStatus & { name: string }>("/api/files/start", { name }));
      setStep(keyReady ? 2 : 1);
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
    setBusy(false);
  }

  async function adopt() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await agent<{ folder: string; copied: string[] }>("/api/legacy/adopt", {});
      const fresh = await agent<Me>("/api/me");
      setMe(fresh);
      setMsg({ ok: true, text: `Copied your files into ${r.folder}/ (the originals are untouched).` });
      setStep(fresh.has_profile ? 6 : 4);
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
    setBusy(false);
  }

  return (
    <div className="page onboarding">
      <header className="page-head">
        <div>
          <h1>Set up Appli</h1>
          <p className="muted">A few minutes, once. Everything is saved on this computer in your own folder; your profile also syncs to your account.</p>
        </div>
      </header>

      <nav className="steps">
        {STEPS.map((s, i) => (
          <button
            key={s}
            className={`stepchip ${i === step ? "on" : ""} ${i < step ? "done" : ""}`}
            disabled={!canGo(i)}
            onClick={() => setStep(i)}
          >
            <span>{i < step ? "✓" : i + 1}</span> {s}
          </button>
        ))}
      </nav>

      {msg && (
        <div className={`banner ${msg.ok ? "ok-b" : "error"}`}>
          {msg.ok ? <IconCheck /> : <IconAlert />} {msg.text}
        </div>
      )}

      {step === 0 && (
        <section className="panel pf">
          {me.legacy_found && (
            <div className="pf-item legacy">
              <b>Found your existing files in the project folder</b>
              <p className="muted small">
                {me.legacy_found.profile}, resumes {me.legacy_found.resumes.join(", ")}
                {me.legacy_found.cover_letter ? " and your cover letter" : ""}. Use them? They're copied into <code>users/&lt;your name&gt;/</code>;
                the originals stay where they are.
              </p>
              <div className="row">
                <button className="btn primary" disabled={busy} onClick={adopt}>
                  {busy ? "Copying…" : "Use my existing files"}
                </button>
              </div>
            </div>
          )}
          <h2>{me.legacy_found ? "Or start fresh" : "What's your name?"}</h2>
          <p className="muted small">Your files go in a folder with this name, and your resumes are named after it (e.g. "Jane Resume SWE").</p>
          <div className="pf-grid">
            <label className="pf-field">
              <span>Full name</span>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Jane Doe" autoFocus={!me.legacy_found} />
            </label>
            <div className="pf-field">
              <span>Signed in as</span>
              <input value={me.email} disabled />
            </div>
          </div>
          <div className="row">
            <button className="btn primary" disabled={!name.trim() || busy} onClick={start}>
              Continue
            </button>
          </div>
        </section>
      )}

      {step === 1 && (
        <section className="panel pf">
          <h2>Your OpenRouter API key</h2>
          <ApiKeyManager me={me} onChange={merge} onSaved={() => setStep(2)} />
          <footer className="pf-nav">
            <button className="btn" onClick={() => setStep(0)}>
              Back
            </button>
            <div className="spacer" />
            <button className="btn primary" disabled={!keyReady} onClick={() => setStep(2)}>
              Next
            </button>
          </footer>
        </section>
      )}

      {step === 2 && (
        <section className="panel pf">
          <h2>Your resumes</h2>
          <ResumeManager me={me} onChange={merge} />
          <footer className="pf-nav">
            <button className="btn" onClick={() => setStep(1)}>
              Back
            </button>
            <div className="spacer" />
            <button className="btn primary" disabled={!me.resumes.length} onClick={() => setStep(3)}>
              Next
            </button>
          </footer>
        </section>
      )}

      {step === 3 && (
        <section className="panel pf">
          <h2>A cover letter you've written (optional)</h2>
          <CoverManager me={me} onChange={merge} />
          <footer className="pf-nav">
            <button className="btn" onClick={() => setStep(2)}>
              Back
            </button>
            <div className="spacer" />
            <button className="btn primary" onClick={() => setStep(4)}>
              {me.cover_letter ? "Next" : "Skip"}
            </button>
          </footer>
        </section>
      )}

      {step === 4 && (
        <ProfileView
          firstTime={!me.has_profile}
          embedded
          onSaved={() => {
            merge({ has_profile: true, profile_md: true });
            setStep(5);
          }}
        />
      )}

      {step === 5 && (
        <section className="panel pf">
          <h2>Jobs to apply to</h2>
          <p className="muted small">Paste links or upload a list now, or skip and use <b>Find jobs</b> after setup to search job boards for roles that fit you. Each run fills the next ones, best matches first.</p>
          <AddJobs onDone={(r) => setJobsAdded((n) => n + r.inserted)} />
          <footer className="pf-nav">
            <button className="btn" onClick={() => setStep(4)}>
              Back
            </button>
            <div className="spacer" />
            <button className="btn primary" onClick={() => setStep(6)}>
              {jobsAdded ? "Next" : "Skip for now"}
            </button>
          </footer>
        </section>
      )}

      {step === 6 && (
        <section className="panel pf done-panel">
          <h2>
            <IconCheck /> You're set
          </h2>
          <ul className="muted">
            <li>
              Files: <code>{me.folder}/</code> ({me.resumes.length} resume{me.resumes.length === 1 ? "" : "s"}
              {me.cover_letter ? ", cover letter" : ""}, profile)
            </li>
            <li>Press <b>Run</b> on Applications: a browser opens and fills each form, then stops before Submit.</li>
            <li>You review every form and submit it yourself. Change files any time on <b>My files</b>, facts on <b>Profile</b>.</li>
          </ul>
          <div className="row">
            <button className="btn primary big" onClick={onFinish}>
              Go to Applications
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
