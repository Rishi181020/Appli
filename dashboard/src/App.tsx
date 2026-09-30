import { useCallback, useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { Me, agent, configured, supabase } from "./lib";
import Login, { ConnectRunner } from "./Login";
import JobsView from "./JobsView";
import AnswersView from "./AnswersView";
import AppliedView from "./AppliedView";
import ResumeReview from "./ResumeReview";
import ProfileView from "./ProfileView";
import FilesView from "./Files";
import Onboarding from "./Onboarding";
import FindJobs from "./FindJobs";
import { IconBookmark, IconCheck, IconDoc, IconFolder, IconList, IconLogout, IconSearch, IconUser } from "./Icons";

type Tab = "jobs" | "find" | "applied" | "resume" | "answers" | "profile" | "files";

export default function App() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [tab, setTab] = useState<Tab>("jobs");
  const [applied, setApplied] = useState<number | null>(null);
  const [pendingResumes, setPendingResumes] = useState<number | null>(null);
  // what this computer has for the signed-in person (from the local server); undefined = loading
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [meError, setMeError] = useState<string | null>(null);
  // decided once per sign-in: the wizard stays up until its last step, even after the profile is saved
  const [setup, setSetup] = useState<boolean | null>(null);

  useEffect(() => {
    if (!configured) return;
    supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => data.subscription.unsubscribe();
  }, []);

  const loadMe = useCallback(async () => {
    try {
      const m = await agent<Me>("/api/me");
      setMe(m);
      setMeError(null);
      setSetup((s) => (s === null ? !m.folder || !m.resumes.length || !m.has_profile : s));
    } catch (e) {
      setMe(null);
      setMeError((e as Error).message);
    }
  }, []);

  const userId = session?.user.id;
  useEffect(() => {
    if (userId) loadMe();
    else {
      setMe(undefined);
      setSetup(null);
    }
  }, [userId, loadMe]);

  useEffect(() => {
    if (!session || setup !== false) return;
    const count = () =>
      supabase
        .from("jobs")
        .select("id", { count: "exact", head: true })
        .eq("status", "submitted")
        .then(({ count }) => setApplied(count ?? null));
    const pending = () =>
      supabase
        .from("jobs")
        .select("id", { count: "exact", head: true })
        .or("resume_review.eq.pending,status.eq.tailoring")
        .then(({ count, error }) => setPendingResumes(error ? null : count ?? 0));
    count();
    pending();
    const t = setInterval(() => {
      count();
      pending();
    }, 10000);
    return () => clearInterval(t);
  }, [session, tab, setup]);

  if (!configured)
    return (
      <div className="center auth-bg">
        <div className="login">
          <div className="logo big-logo">A</div>
          <h1>One setup step</h1>
          <p className="muted">
            Put the Supabase URL and publishable key you were given in the project's <code>.env</code> (see <code>.env.example</code>),
            then run <code>start.bat</code> again.
          </p>
        </div>
      </div>
    );
  if (session === undefined) return <div className="center muted">Loading…</div>;
  if (!session) return <Login />;
  if (me === null)
    return (
      <div className="center auth-bg">
        <div className="login">
          <div className="logo big-logo">A</div>
          <h1>Appli isn't running on this computer</h1>
          <p className="muted">
            Open it with <code>start.bat</code> (Windows) or <code>./start.sh</code> (Mac/Linux) and use the page it opens.
          </p>
          {meError && <p className="small muted">({meError})</p>}
          <div className="row">
            <button className="btn primary" onClick={loadMe}>
              Try again
            </button>
            <button className="btn ghost" onClick={() => supabase.auth.signOut({ scope: "local" })}>
              Sign out
            </button>
          </div>
        </div>
      </div>
    );
  if (me === undefined || setup === null) return <div className="center muted">Loading…</div>;

  const email = session.user.email ?? "";
  const banners = (
    <>
      {!me.db_ready && (
        <div className="banner error top-banner">
          The database isn't set up for several people yet: the project owner needs to run <code>supabase/migrations/004_multi_user.sql</code>.
        </div>
      )}
      {!me.runner_connected && <ConnectRunner email={email} onDone={loadMe} />}
    </>
  );

  const nav = (t: Tab, icon: JSX.Element, label: string, extra?: JSX.Element | null) => (
    <button className={tab === t ? "nav on" : "nav"} onClick={() => setTab(t)}>
      {icon} {label}
      {extra}
    </button>
  );

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <div className="logo">A</div>
          <span>Appli</span>
        </div>
        <nav>
          {setup ? (
            <button className="nav on">
              <IconUser /> Set up
            </button>
          ) : (
            <>
              {nav("jobs", <IconList />, "Applications")}
              {nav("find", <IconSearch />, "Find jobs")}
              {nav("applied", <IconCheck />, "Applied", applied !== null ? <span className="count">{applied}</span> : null)}
              {nav("resume", <IconDoc />, "Resume review", pendingResumes ? <span className="count warn-count">{pendingResumes}</span> : null)}
              {nav("answers", <IconBookmark />, "Saved answers")}
              {nav("profile", <IconUser />, "Profile")}
              {nav("files", <IconFolder />, "My files")}
            </>
          )}
        </nav>
        <div className="spacer" />
        <div className="me">
          <div className="me-mail" title={email}>
            {me.name || email}
          </div>
          <button className="nav" onClick={() => supabase.auth.signOut({ scope: "local" })}>
            <IconLogout /> Sign out
          </button>
        </div>
      </aside>
      <main className="main">
        {banners}
        {setup ? (
          <Onboarding
            me={me}
            setMe={setMe}
            onFinish={() => {
              setSetup(false);
              setTab("jobs");
              loadMe();
            }}
          />
        ) : tab === "profile" ? (
          <ProfileView firstTime={false} onSaved={loadMe} />
        ) : tab === "files" ? (
          <FilesView me={me} onChange={(s) => setMe({ ...me, ...s })} />
        ) : tab === "find" ? (
          <FindJobs />
        ) : tab === "jobs" ? (
          <JobsView onOpenApplied={() => setTab("applied")} />
        ) : tab === "applied" ? (
          <AppliedView />
        ) : tab === "resume" ? (
          <ResumeReview />
        ) : (
          <AnswersView />
        )}
      </main>
    </div>
  );
}
