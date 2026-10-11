import { useCallback, useEffect, useMemo, useState } from "react";
import { BankData, BankEntry, BANK_KINDS, agent, fileToBase64 } from "./lib";
import { IconAlert, IconCheck, IconRefresh, IconSearch, IconX } from "./Icons";

const blank = (kind = "experience"): BankEntry => ({ id: "", kind, title: "", org: "", location: "", start: "", end: "", link: "", text: "", tags: [], source: "typed", updated: "" });
const hasOrg = (k: string) => ["experience", "research", "leadership", "education", "award", "publication"].includes(k);

function EntryForm({ entry, onSave, onCancel, busy }: { entry: BankEntry; onSave: (e: BankEntry) => void; onCancel: () => void; busy: boolean }) {
  const [e, setE] = useState(entry);
  const set = (k: keyof BankEntry, v: string) => setE((x) => ({ ...x, [k]: v }));
  return (
    <div className="pf-item">
      <div className="pf-grid">
        <label className="pf-field">
          <span>Kind</span>
          <select value={e.kind} onChange={(x) => set("kind", x.target.value)}>
            {BANK_KINDS.map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="pf-field">
          <span>Title</span>
          <input value={e.title} onChange={(x) => set("title", x.target.value)} placeholder={e.kind === "project" ? "Temple Finance" : "Software Engineer Intern"} />
        </label>
        {hasOrg(e.kind) && (
          <label className="pf-field">
            <span>{e.kind === "publication" ? "Venue" : "Organization"}</span>
            <input value={e.org} onChange={(x) => set("org", x.target.value)} />
          </label>
        )}
        <label className="pf-field">
          <span>Location</span>
          <input value={e.location} onChange={(x) => set("location", x.target.value)} />
        </label>
        <label className="pf-field">
          <span>Start</span>
          <input value={e.start} onChange={(x) => set("start", x.target.value)} placeholder="Jun 2025" />
        </label>
        <label className="pf-field">
          <span>End</span>
          <input value={e.end} onChange={(x) => set("end", x.target.value)} placeholder="Present" />
        </label>
        <label className="pf-field wide">
          <span>Link</span>
          <input value={e.link} onChange={(x) => set("link", x.target.value)} placeholder="GitHub, paper or demo link (optional)" />
        </label>
        <label className="pf-field wide">
          <span>Everything you did (the more detail, the better: numbers, tools, what you built and why)</span>
          <textarea rows={8} value={e.text} onChange={(x) => set("text", x.target.value)} />
          <small>Only what's written here can end up on a resume, so be complete and accurate.</small>
        </label>
        <label className="pf-field wide">
          <span>Tags (comma separated)</span>
          <input value={e.tags.join(", ")} onChange={(x) => setE((y) => ({ ...y, tags: x.target.value.split(",").map((t) => t.trim()).filter(Boolean) }))} />
        </label>
      </div>
      <div className="row wrap">
        <button className="btn primary small-btn" disabled={busy || !(e.title.trim() || e.text.trim())} onClick={() => onSave(e)}>
          {busy ? "Saving…" : "Save entry"}
        </button>
        <button className="btn ghost small-btn" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/** Everything you've done, in your own words: resumes, papers, projects and long descriptions. Resumes for
 *  low-match jobs are written from here, using only what it says. */
export default function BankView() {
  const [data, setData] = useState<BankData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [editing, setEditing] = useState<BankEntry | null>(null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      setData(await agent<BankData>("/api/bank"));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  async function run(what: string, fn: () => Promise<string>) {
    setBusy(what);
    setMsg(null);
    try {
      setMsg({ ok: true, text: await fn() });
      await load();
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
    setBusy(null);
  }

  const importExisting = () =>
    run("import", async () => {
      const r = await agent<{ imported: { file: string; added?: number; updated?: number; error?: string }[] }>("/api/bank/import", {});
      return r.imported.map((x) => (x.error ? `${x.file}: ${x.error}` : `${x.file}: ${x.added} new, ${x.updated} updated`)).join(" · ");
    });

  const upload = (files: File[]) =>
    run("upload", async () => {
      const out: string[] = [];
      for (const [i, f] of files.entries()) {
        setMsg({ ok: true, text: `Reading ${f.name} (${i + 1} of ${files.length})…` });
        try {
          const r = await agent<{ added: number; updated: number }>("/api/bank/upload", { filename: f.name, data: await fileToBase64(f) });
          out.push(`${f.name}: ${r.added} new, ${r.updated} updated`);
        } catch (e) {
          out.push(`${f.name}: ${(e as Error).message}`);
        }
      }
      return out.join(" · ");
    });

  const save = (e: BankEntry) =>
    run("save", async () => {
      await agent("/api/bank/entry", { entry: e });
      setEditing(null);
      return "Saved.";
    });

  const remove = (e: BankEntry) => {
    if (!confirm(`Delete "${e.title || e.kind}" from your bank?`)) return;
    run("delete", async () => {
      await agent("/api/bank/entry/delete", { id: e.id });
      return "Deleted.";
    });
  };

  const removeFile = (name: string) => {
    if (!confirm(`Remove ${name}? Entries that came only from it are deleted too.`)) return;
    run("file", async () => {
      await agent("/api/bank/file/delete", { name });
      return `Removed ${name}.`;
    });
  };

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data?.entries ?? []).filter(
      (e) => (kind === "all" || e.kind === kind) && (!q || [e.title, e.org, e.text, e.tags.join(" ")].join(" ").toLowerCase().includes(q))
    );
  }, [data, query, kind]);

  const groups = BANK_KINDS.map(([k, label]) => [label, shown.filter((e) => e.kind === k)] as const).filter(([, list]) => list.length);
  const counts = data?.summary.by_kind ?? {};
  const solid = Object.entries(counts).filter(([k]) => !["skills", "narrative", "education"].includes(k)).reduce((a, [, n]) => a + n, 0);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Experience bank</h1>
          <p className="muted">
            Everything you've done, in full: every resume, papers, projects and long descriptions. When your best resume matches a job
            poorly, Appli writes a new one for it from here, picks the layout for the job, has a second model check it, and puts it in Resume
            review for you to approve. Nothing outside this bank is ever added.
          </p>
        </div>
        <button className="btn ghost icon-btn" onClick={load} title="Refresh">
          <IconRefresh />
        </button>
      </header>

      {error && <div className="banner error">Couldn't load your bank: {error}</div>}

      <div className="row wrap">
        <button className="btn primary" disabled={!!busy} onClick={importExisting} title="Your resumes, profile and cover letter. Safe to repeat.">
          {busy === "import" ? "Importing…" : "Import my resumes, profile and cover letter"}
        </button>
        <label className={`btn ${busy ? "disabled" : ""}`}>
          {busy === "upload" ? "Reading…" : "Upload documents"}
          <input
            type="file"
            multiple
            hidden
            accept=".pdf,.docx,.tex,.txt,.md"
            disabled={!!busy}
            onChange={(e) => {
              const fs = [...(e.target.files ?? [])];
              e.target.value = "";
              if (fs.length) upload(fs);
            }}
          />
        </label>
        <button className="btn" disabled={!!busy} onClick={() => setEditing(blank())}>
          Type an entry
        </button>
        <div className="spacer" />
        {data && (
          <span className={`chip-tag ${solid >= 2 ? "ok-tag" : ""}`}>
            {solid >= 2 ? <IconCheck /> : null}
            {solid >= 2 ? "Ready: low-match jobs get a resume built from here" : "Add at least 2 roles, projects or papers to start building resumes"}
          </span>
        )}
      </div>
      <p className="small muted">Uploads can be PDF, Word (.docx), LaTeX, text or Markdown. Text is copied word for word into entries; nothing is summarized away.</p>

      {msg && (
        <div className={`banner ${msg.ok ? "ok-b" : "error"}`}>
          {msg.ok ? <IconCheck /> : <IconAlert />} {msg.text}
        </div>
      )}

      {editing && <EntryForm key={editing.id || "new"} entry={editing} busy={busy === "save"} onSave={save} onCancel={() => setEditing(null)} />}

      {data && data.entries.length > 0 && (
        <div className="row wrap">
          <label className="search">
            <IconSearch />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search your bank" />
          </label>
          <div className="seg">
            <button className={kind === "all" ? "on" : ""} onClick={() => setKind("all")}>
              All {data.entries.length}
            </button>
            {BANK_KINDS.filter(([k]) => counts[k]).map(([k, label]) => (
              <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>
                {label} {counts[k]}
              </button>
            ))}
          </div>
        </div>
      )}

      {!data ? (
        !error && <div className="skeleton row-skel" />
      ) : data.entries.length === 0 ? (
        <div className="empty">
          <div className="empty-art">✦</div>
          <b>Your bank is empty</b>
          <span className="muted">Start with "Import my resumes, profile and cover letter", then upload anything else: papers, old resumes, project write-ups.</span>
        </div>
      ) : (
        groups.map(([label, list]) => (
          <section key={label} className="bank-group">
            <h3>{label}</h3>
            <div className="list">
              {list.map((e) =>
                editing?.id === e.id ? null : (
                  <div key={e.id} className="bank-row">
                    <div className="bank-row-head">
                      <button
                        className="bank-row-title"
                        onClick={() =>
                          setOpenIds((s) => {
                            const n = new Set(s);
                            if (n.has(e.id)) n.delete(e.id);
                            else n.add(e.id);
                            return n;
                          })
                        }
                      >
                        <b>{e.title || "(untitled)"}</b>
                        {e.org && <span className="muted"> · {e.org}</span>}
                        {(e.start || e.end) && <span className="small muted"> · {[e.start, e.end].filter(Boolean).join(" - ")}</span>}
                      </button>
                      <span className="chip-tag" title="Where this came from">
                        {e.source}
                      </span>
                      <button className="btn ghost small-btn" disabled={!!busy} onClick={() => setEditing(e)}>
                        Edit
                      </button>
                      <button className="btn ghost icon-btn" aria-label="Delete entry" disabled={!!busy} onClick={() => remove(e)}>
                        <IconX />
                      </button>
                    </div>
                    <p className={`bank-text ${openIds.has(e.id) ? "open" : ""}`}>{e.text}</p>
                  </div>
                )
              )}
            </div>
          </section>
        ))
      )}

      {data && data.files.length > 0 && (
        <section className="bank-group">
          <h3>Uploaded documents</h3>
          <div className="list">
            {data.files.map((f) => (
              <div key={f.name} className="bank-row bank-file">
                <code>{f.name}</code>
                <span className="small muted">
                  {f.entries.length} entr{f.entries.length === 1 ? "y" : "ies"} · {f.added}
                </span>
                <div className="spacer" />
                <button className="btn ghost icon-btn" aria-label={`Remove ${f.name}`} disabled={!!busy} onClick={() => removeFile(f.name)}>
                  <IconX />
                </button>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
