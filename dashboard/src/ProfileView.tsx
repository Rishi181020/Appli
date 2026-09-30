import { useEffect, useMemo, useState } from "react";
import { agent, supabase } from "./lib";
import {
  AUTH_QUESTIONS,
  COMMON,
  Education,
  Experience,
  ProfileData,
  emptyProfile,
  normalizeProfile,
  renderProfile,
  validateProfile,
} from "./profileMd";
import { IconAlert, IconCheck } from "./Icons";

const STEPS = ["Start", "Basics", "Education", "Experience", "Skills", "Work authorization", "Common answers", "Review"] as const;

function Text({
  label,
  value,
  onChange,
  placeholder,
  wide,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  wide?: boolean;
}) {
  return (
    <label className={`pf-field ${wide ? "wide" : ""}`}>
      <span>{label}</span>
      <input value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

export default function ProfileView({
  onSaved,
  firstTime,
  embedded,
}: {
  onSaved: () => void;
  firstTime: boolean;
  /** inside the onboarding wizard: no page header, and the resume is read automatically */
  embedded?: boolean;
}) {
  const [step, setStep] = useState(0);
  const [data, setData] = useState<ProfileData>(emptyProfile());
  const [savedMd, setSavedMd] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    supabase
      .from("profiles")
      .select("data,markdown")
      .maybeSingle()
      .then(({ data: row, error }) => {
        if (error) setMsg({ ok: false, text: `${error.message} (has migration 004 been run?)` });
        if (row?.data && Object.keys(row.data).length) {
          setData(normalizeProfile(row.data as ProfileData));
          setSavedMd(row.markdown ?? "");
          if (!firstTime || embedded) setStep(1);
        } else if (embedded) {
          importResume(); // first time: start from the resume that was just uploaded
        }
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firstTime, embedded]);

  const md = useMemo(() => renderProfile(data), [data]);
  const errors = useMemo(() => validateProfile(data), [data]);
  const set = (p: Partial<ProfileData>) => setData((d) => ({ ...d, ...p }));

  async function importResume() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await agent<{ data: Partial<ProfileData>; resume: string }>("/api/profile/import", {});
      const imported = normalizeProfile(r.data);
      // keep anything already typed; fill the rest from the resume
      setData((d) => ({
        ...imported,
        ...Object.fromEntries(Object.entries(d).filter(([, v]) => (Array.isArray(v) ? v.length : typeof v === "object" ? Object.keys(v).length : v))),
      }) as ProfileData);
      setMsg({ ok: true, text: `Filled from ${r.resume}. Check every step before saving.` });
      setStep(1);
    } catch (e) {
      setMsg({ ok: false, text: `Couldn't read your resume: ${(e as Error).message}. You can fill the steps in by hand.` });
    }
    setBusy(false);
  }

  async function save() {
    if (errors.length) {
      setMsg({ ok: false, text: "Fix the items listed below first." });
      return;
    }
    setBusy(true);
    setMsg(null);
    const { data: auth } = await supabase.auth.getUser();
    const owner = auth.user?.id;
    const { error } = await supabase
      .from("profiles")
      .upsert({ owner_id: owner, data, markdown: md }, { onConflict: "owner_id" });
    if (error) {
      setMsg({ ok: false, text: error.message });
      setBusy(false);
      return;
    }
    // common answers become '~rules' in Saved answers, so forms get them instantly
    const rules = COMMON.filter((c) => (data.common[c.key] ?? "").trim()).map((c) => ({
      owner_id: owner,
      question_key: c.rule,
      question_text: c.rule,
      answer: data.common[c.key].trim(),
      source: "manual",
    }));
    if (rules.length) await supabase.from("answers").upsert(rules, { onConflict: "owner_id,question_key" });
    let where = "";
    try {
      const r = await agent<{ path: string }>("/api/profile/sync", {});
      where = ` and written to ${r.path}`;
    } catch {
      where = ". It will be written to this computer at the start of the next run";
    }
    setSavedMd(md);
    setMsg({ ok: true, text: `Profile saved${where}.` });
    setBusy(false);
    onSaved();
  }

  if (loading) return <div className="page"><div className="skeleton hero-skel" /></div>;

  const changed = savedMd && savedMd !== md;

  return (
    <div className={embedded ? "pf-embedded" : "page"}>
      {embedded ? (
        <p className="muted">
          Everything here becomes your <code>profile.md</code>: the only facts the models use to fill forms, answer questions and write
          cover letters. Check each step, then save.
        </p>
      ) : (
        <header className="page-head">
          <div>
            <h1>{firstTime ? "Set up your profile" : "Profile"}</h1>
            <p className="muted">
              Everything here becomes your <code>profile.md</code>: the only facts the models use to fill forms, answer questions and
              write cover letters. Nothing outside it is ever claimed.
            </p>
          </div>
        </header>
      )}

      <nav className="steps">
        {STEPS.map((s, i) => (
          <button key={s} className={`stepchip ${i === step ? "on" : ""} ${i < step ? "done" : ""}`} onClick={() => setStep(i)}>
            <span>{i < step ? "✓" : i + 1}</span> {s}
          </button>
        ))}
      </nav>

      {msg && (
        <div className={`banner ${msg.ok ? "ok-b" : "error"}`}>
          {msg.ok ? <IconCheck /> : <IconAlert />} {msg.text}
        </div>
      )}

      <section className="panel pf">
        {step === 0 && (
          <div className="pf-start">
            <h2>How do you want to start?</h2>
            <p className="muted">
              Importing reads your first resume (My files) and fills the form. You still check every step.
            </p>
            <div className="row">
              <button className="btn primary big" disabled={busy} onClick={importResume}>
                {busy ? "Reading your resume…" : "Import from my resume"}
              </button>
              <button className="btn big" onClick={() => setStep(1)}>
                Start blank
              </button>
            </div>
          </div>
        )}

        {step === 1 && (
          <div className="pf-grid">
            <Text label="Full name" value={data.name} onChange={(v) => set({ name: v })} placeholder="Jane Doe" />
            <Text label="Email" value={data.email} onChange={(v) => set({ email: v })} placeholder="jane@example.com" />
            <Text label="Phone (with country code)" value={data.phone} onChange={(v) => set({ phone: v })} placeholder="+1 408 555 0123" />
            <Text label="City, State" value={data.location} onChange={(v) => set({ location: v })} placeholder="San Jose, CA" />
            <Text label="LinkedIn URL" value={data.linkedin} onChange={(v) => set({ linkedin: v })} placeholder="https://www.linkedin.com/in/…" />
            <Text label="GitHub URL" value={data.github} onChange={(v) => set({ github: v })} placeholder="https://github.com/…" />
            <Text label="Website / portfolio" value={data.website} onChange={(v) => set({ website: v })} placeholder="https://…" wide />
          </div>
        )}

        {step === 2 && (
          <ListEditor<Education>
            items={data.education}
            onChange={(education) => set({ education })}
            blank={{ school: "", degree: "", field: "", start: "", end: "", gpa: "" }}
            addLabel="Add education"
            hint="Most recent first. Use the school's official name: forms match it against their school lists."
            render={(e, up) => (
              <div className="pf-grid">
                <Text label="School (official name)" value={e.school} onChange={(v) => up({ school: v })} wide />
                <Text label="Degree" value={e.degree} onChange={(v) => up({ degree: v })} placeholder="Master of Science" />
                <Text label="Field of study" value={e.field} onChange={(v) => up({ field: v })} placeholder="Computer Science" />
                <Text label="Start (YYYY-MM)" value={e.start} onChange={(v) => up({ start: v })} placeholder="2025-09" />
                <Text label="End (YYYY-MM, expected ok)" value={e.end} onChange={(v) => up({ end: v })} placeholder="2027-06" />
                <Text label="GPA (optional)" value={e.gpa} onChange={(v) => up({ gpa: v })} placeholder="3.7" />
              </div>
            )}
          />
        )}

        {step === 3 && (
          <ListEditor<Experience>
            items={data.experience}
            onChange={(experience) => set({ experience })}
            blank={{ title: "", company: "", start: "", end: "", bullets: [""] }}
            addLabel="Add a role"
            hint="Most recent first. Bullets are the facts cover letters and essays may use: include real numbers."
            render={(x, up) => (
              <>
                <div className="pf-grid">
                  <Text label="Title" value={x.title} onChange={(v) => up({ title: v })} />
                  <Text label="Company" value={x.company} onChange={(v) => up({ company: v })} />
                  <Text label="Start (YYYY-MM)" value={x.start} onChange={(v) => up({ start: v })} />
                  <Text label="End (YYYY-MM or Present)" value={x.end} onChange={(v) => up({ end: v })} placeholder="Present" />
                </div>
                <label className="pf-field wide">
                  <span>Bullets (one per line)</span>
                  <textarea
                    rows={4}
                    value={x.bullets.join("\n")}
                    onChange={(e) => up({ bullets: e.target.value.split("\n") })}
                  />
                </label>
              </>
            )}
          />
        )}

        {step === 4 && (
          <ListEditor
            items={data.skills}
            onChange={(skills) => set({ skills })}
            blank={{ group: "", items: "" }}
            addLabel="Add a skills line"
            hint="Group them like your resume: Languages, Frontend, Backend, Databases, Tools and cloud…"
            render={(s, up) => (
              <div className="pf-grid">
                <Text label="Group" value={s.group} onChange={(v) => up({ group: v })} placeholder="Languages" />
                <Text label="Skills (comma separated)" value={s.items} onChange={(v) => up({ items: v })} placeholder="Python, TypeScript, SQL" wide />
              </div>
            )}
          />
        )}

        {step === 5 && (
          <div className="pf-auth">
            <p className="muted small">
              Used for the matching questions on every form. The first four also decide which postings are skipped for you.
            </p>
            {AUTH_QUESTIONS.map((a) => (
              <label key={a.q} className="pf-q">
                <span>
                  {a.q} {a.required && <em className="req">required</em>}
                  {a.hint && <small className="muted">{a.hint}</small>}
                </span>
                <select
                  value={data.authorization[a.q] ?? ""}
                  onChange={(e) => set({ authorization: { ...data.authorization, [a.q]: e.target.value } })}
                >
                  <option value="">—</option>
                  {a.options.map((o) => (
                    <option key={o}>{o}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        )}

        {step === 6 && (
          <div className="pf-grid">
            {COMMON.map((c) => (
              <Text
                key={c.key}
                label={c.label}
                value={data.common[c.key] ?? ""}
                placeholder={c.placeholder}
                onChange={(v) => set({ common: { ...data.common, [c.key]: v } })}
              />
            ))}
            <label className="pf-field wide">
              <span>Anything else the models should know (optional)</span>
              <textarea rows={3} value={data.notes} onChange={(e) => set({ notes: e.target.value })} />
            </label>
            <p className="muted small wide">Each answer is also saved as a rule in Saved answers, so forms get it instantly.</p>
          </div>
        )}

        {step === 7 && (
          <div className="pf-review">
            {errors.length > 0 && (
              <ul className="problems">
                {errors.map((e) => (
                  <li key={e}>
                    <IconAlert />
                    <div>{e}</div>
                  </li>
                ))}
              </ul>
            )}
            <p className="muted small">
              This is exactly what the models will read{changed ? " (changed since your last save)" : ""}.
            </p>
            <pre className="md-preview">{md}</pre>
          </div>
        )}
      </section>

      <footer className="pf-nav">
        <button className="btn" disabled={step === 0} onClick={() => setStep((s) => Math.max(0, s - 1))}>
          Back
        </button>
        <div className="spacer" />
        {step < STEPS.length - 1 ? (
          <button className="btn primary" onClick={() => setStep((s) => s + 1)}>
            Next
          </button>
        ) : (
          <button className="btn primary big" disabled={busy || errors.length > 0} onClick={save}>
            {busy ? "Saving…" : "Save profile"}
          </button>
        )}
      </footer>
    </div>
  );
}

function ListEditor<T>({
  items,
  onChange,
  blank,
  render,
  addLabel,
  hint,
}: {
  items: T[];
  onChange: (items: T[]) => void;
  blank: T;
  render: (item: T, update: (p: Partial<T>) => void) => React.ReactNode;
  addLabel: string;
  hint: string;
}) {
  const update = (i: number, p: Partial<T>) => onChange(items.map((it, k) => (k === i ? { ...it, ...p } : it)));
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= items.length) return;
    const next = [...items];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  return (
    <div className="pf-list">
      <p className="muted small">{hint}</p>
      {items.map((it, i) => (
        <div key={i} className="pf-item">
          <div className="pf-item-head">
            <b>#{i + 1}</b>
            <div className="spacer" />
            <button className="btn ghost small-btn" onClick={() => move(i, -1)} disabled={i === 0}>
              ↑
            </button>
            <button className="btn ghost small-btn" onClick={() => move(i, 1)} disabled={i === items.length - 1}>
              ↓
            </button>
            <button className="btn ghost small-btn" onClick={() => onChange(items.filter((_, k) => k !== i))}>
              Remove
            </button>
          </div>
          {render(it, (p) => update(i, p))}
        </div>
      ))}
      <button className="btn" onClick={() => onChange([...items, { ...blank }])}>
        + {addLabel}
      </button>
    </div>
  );
}
