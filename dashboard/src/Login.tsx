import { FormEvent, useState } from "react";
import { connectRunner, supabase } from "./lib";
import { IconBolt, IconShield, IconTarget, Logo } from "./Icons";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      setError(error.message);
      setBusy(false);
      return;
    }
    // the runner gets its own session; if the local server isn't up yet, the app asks again later
    await connectRunner(email, password).catch(() => undefined);
    setBusy(false);
  }

  return (
    <div className="auth">
      <section className="auth-hero">
        <div className="brand">
          <div className="logo">
            <Logo />
          </div>
          <span>Appli</span>
        </div>
        <div>
          <h1>
            Your applications, <em>filled for you.</em> Submitted by you.
          </h1>
          <p className="lead">
            Appli finds roles that fit, tailors your resume, and fills every form from your own profile. It always stops before Submit.
          </p>
        </div>
        <ul className="auth-points">
          <li>
            <span className="pt-icon">
              <IconTarget />
            </span>
            <div>
              <b>Matched to your resumes</b>
              <span>Every posting is scored ATS-style, so the best resume goes in each time.</span>
            </div>
          </li>
          <li>
            <span className="pt-icon">
              <IconBolt />
            </span>
            <div>
              <b>Forms filled in seconds</b>
              <span>Greenhouse, Lever, Ashby, Workable, and Workday page by page.</span>
            </div>
          </li>
          <li>
            <span className="pt-icon">
              <IconShield />
            </span>
            <div>
              <b>You stay in control</b>
              <span>Nothing is ever submitted for you, and nothing is guessed.</span>
            </div>
          </li>
        </ul>
        <p className="auth-foot">Your files stay on your computer. Your jobs and profile are private to your account.</p>
      </section>
      <div className="auth-side">
      <form className="login" onSubmit={submit}>
        <h1>Welcome back</h1>
        <p className="muted">Sign in with the email and password you were given.</p>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div className="banner error">{error}</div>}
        <button className="btn primary big" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="small muted">No account? Ask the person who shared Appli with you to add you.</p>
      </form>
      </div>
    </div>
  );
}

/** Shown when the dashboard is signed in but this computer's runner has no session yet (asks for the password once). */
export function ConnectRunner({ email, onDone }: { email: string; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await connectRunner(email, password);
      onDone();
    } catch (err) {
      setError((err as Error).message);
    }
    setBusy(false);
  }

  return (
    <form className="banner warn connect" onSubmit={submit}>
      <span>
        <b>Connect the runner on this computer.</b> Enter your password once so runs can sign in as you (it isn't stored).
      </span>
      <input type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      <button className="btn primary small-btn" disabled={busy}>
        {busy ? "Connecting…" : "Connect"}
      </button>
      {error && <span className="connect-err">{error}</span>}
    </form>
  );
}
