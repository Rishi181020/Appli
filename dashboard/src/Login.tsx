import { FormEvent, useState } from "react";
import { connectRunner, supabase } from "./lib";
import { IconBolt, IconShield, IconTarget, Logo } from "./Icons";

/** Supabase's sign-up errors, in plain words (and what the project owner must change when sign-ups are off). */
function signUpError(message: string): string {
  if (/signups? not allowed|signup.*disabled/i.test(message))
    return "New accounts are turned off for this Appli. The project owner can turn them on in Supabase: Authentication > Sign In / Providers > Allow new users to sign up.";
  if (/already registered|already exists/i.test(message)) return "There's already an account with this email: sign in instead.";
  if (/password/i.test(message)) return message.replace(/^.*?password/i, "Password");
  return message;
}

export default function Login() {
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const signup = mode === "signup";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    if (signup) {
      if (password.length < 8) return setError("Use at least 8 characters for your password.");
      if (password !== confirm) return setError("The two passwords don't match.");
    }
    setBusy(true);
    if (signup) {
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: { emailRedirectTo: window.location.origin },
      });
      if (error) {
        setError(signUpError(error.message));
        setBusy(false);
        return;
      }
      if (!data.session) {
        // the project asks new accounts to confirm their email first
        setInfo(`Check ${email} for a confirmation link, open it, then sign in here.`);
        setMode("signin");
        setConfirm("");
        setBusy(false);
        return;
      }
    } else {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) {
        setError(/confirm/i.test(error.message) ? "Confirm your email first: open the link we sent you, then sign in." : error.message);
        setBusy(false);
        return;
      }
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
        <div className="seg auth-tabs" role="tablist">
          <button type="button" className={!signup ? "on" : ""} aria-pressed={!signup} onClick={() => { setMode("signin"); setError(null); }}>
            Sign in
          </button>
          <button type="button" className={signup ? "on" : ""} aria-pressed={signup} onClick={() => { setMode("signup"); setError(null); setInfo(null); }}>
            Create account
          </button>
        </div>
        <h1>{signup ? "Create your account" : "Welcome back"}</h1>
        <p className="muted">
          {signup
            ? "Your jobs, answers and profile are private to your account. You'll add your API key and resumes next."
            : "Sign in with your email and password."}
        </p>
        {info && <div className="banner ok-b">{info}</div>}
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={signup ? "new-password" : "current-password"}
            minLength={signup ? 8 : undefined}
            required
          />
        </label>
        {signup && (
          <label>
            Confirm password
            <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />
          </label>
        )}
        {error && <div className="banner error">{error}</div>}
        <button className="btn primary big" disabled={busy}>
          {busy ? (signup ? "Creating your account…" : "Signing in…") : signup ? "Create account" : "Sign in"}
        </button>
        <p className="small muted">
          {signup ? (
            <>Already have an account? <a href="#" onClick={(e) => { e.preventDefault(); setMode("signin"); }}>Sign in</a></>
          ) : (
            <>New here? <a href="#" onClick={(e) => { e.preventDefault(); setMode("signup"); }}>Create an account</a></>
          )}
        </p>
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
