import { useState } from "react";
import { agent, fileToBase64 } from "./lib";
import { IconAlert, IconCheck, IconX } from "./Icons";

type Row = { company: string; role: string | null; url: string | null; location: string | null; ats: string; automated: boolean };
type Preview = {
  count: number;
  automated: number;
  rows: Row[];
  headers?: string[];
  mapping?: Record<string, string>;
  sheets?: string[];
  sheet?: string | null;
  problems?: string[];
};
type Result = { inserted: number; already_present: number; duplicates_skipped?: number };

const FIELDS: { key: string; label: string; required?: boolean }[] = [
  { key: "company", label: "Company", required: true },
  { key: "url", label: "Apply link", required: true },
  { key: "role", label: "Role" },
  { key: "location", label: "Location" },
  { key: "tier", label: "Priority / tier (1 = first)" },
];

/** Add jobs by pasting links or uploading a CSV/xlsx. Used in onboarding and from the Applications page. */
export default function AddJobs({ onDone, onClose }: { onDone?: (r: Result) => void; onClose?: () => void }) {
  const [mode, setMode] = useState<"links" | "file">("links");
  const [links, setLinks] = useState("");
  const [file, setFile] = useState<{ name: string; data: string } | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [sheet, setSheet] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const body = (over: Partial<{ mapping: Record<string, string>; sheet: string | null }> = {}) =>
    mode === "links"
      ? { links }
      : { filename: file?.name, data: file?.data, mapping: over.mapping ?? mapping, sheet: over.sheet ?? sheet };

  async function doPreview(over: Partial<{ mapping: Record<string, string>; sheet: string | null }> = {}) {
    setBusy(true);
    setMsg(null);
    try {
      const p = await agent<Preview>("/api/jobs/preview", body(over));
      setPreview(p);
      if (p.mapping) setMapping(p.mapping);
      if (p.sheet !== undefined) setSheet(p.sheet ?? null);
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
    setBusy(false);
  }

  async function doImport() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await agent<Result>("/api/jobs/import", body());
      setMsg({
        ok: true,
        text: `Added ${r.inserted} job(s)${r.already_present ? `, ${r.already_present} were already in your list` : ""}.`,
      });
      setPreview(null);
      setLinks("");
      setFile(null);
      onDone?.(r);
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
    setBusy(false);
  }

  async function pick(f: File) {
    setPreview(null);
    setMapping({});
    setSheet(null);
    const data = await fileToBase64(f);
    setFile({ name: f.name, data });
    setBusy(true);
    try {
      const p = await agent<Preview>("/api/jobs/preview", { filename: f.name, data });
      setPreview(p);
      setMapping(p.mapping ?? {});
      setSheet(p.sheet ?? null);
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
    setBusy(false);
  }

  const problems = preview?.problems ?? [];
  const manual = preview ? preview.count - preview.automated : 0;

  return (
    <div className="addjobs">
      <div className="row">
        <div className="seg">
          <button className={mode === "links" ? "on" : ""} onClick={() => { setMode("links"); setPreview(null); }}>
            Paste links
          </button>
          <button className={mode === "file" ? "on" : ""} onClick={() => { setMode("file"); setPreview(null); }}>
            Upload CSV / Excel
          </button>
        </div>
        <div className="spacer" />
        {onClose && (
          <button className="btn ghost icon-btn" onClick={onClose} aria-label="Close">
            <IconX />
          </button>
        )}
      </div>

      {mode === "links" ? (
        <>
          <label className="pf-field wide">
            <span>Job links, one per line (Greenhouse, Lever, Ashby and Workable are filled automatically; others are listed for you to do by hand)</span>
            <textarea
              rows={6}
              value={links}
              onChange={(e) => {
                setLinks(e.target.value);
                setPreview(null);
              }}
              placeholder={"https://job-boards.greenhouse.io/company/jobs/123\nhttps://jobs.lever.co/company/abc\nhttps://jobs.ashbyhq.com/company/xyz"}
            />
          </label>
          {!preview && (
            <div className="row">
              <button className="btn primary" disabled={!links.trim() || busy} onClick={() => doPreview()}>
                {busy ? "Reading the links…" : "Preview"}
              </button>
            </div>
          )}
        </>
      ) : (
        <>
          <div className="row wrap">
            <label className={`btn ${file ? "" : "primary"}`}>
              {file ? `File: ${file.name}` : "Choose a .csv or .xlsx"}
              <input
                type="file"
                hidden
                accept=".csv,.xlsx,.xlsm"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (f) pick(f);
                }}
              />
            </label>
            <span className="muted small">Needs a company column and an apply-link column. Any header names work: you'll confirm them.</span>
          </div>
          {preview?.headers && (
            <div className="pf-grid">
              {preview.sheets && preview.sheets.length > 1 && (
                <label className="pf-field">
                  <span>Sheet</span>
                  <select value={sheet ?? ""} onChange={(e) => { setSheet(e.target.value); doPreview({ sheet: e.target.value, mapping: {} }); }}>
                    {preview.sheets.map((s) => (
                      <option key={s}>{s}</option>
                    ))}
                  </select>
                </label>
              )}
              {FIELDS.map((f) => (
                <label key={f.key} className="pf-field">
                  <span>
                    {f.label} {f.required && <em className="req">required</em>}
                  </span>
                  <select
                    value={mapping[f.key] ?? ""}
                    onChange={(e) => {
                      const next = { ...mapping, [f.key]: e.target.value };
                      if (!e.target.value) delete next[f.key];
                      setMapping(next);
                      doPreview({ mapping: next });
                    }}
                  >
                    <option value="">— not in this file —</option>
                    {preview.headers!.filter(Boolean).map((h) => (
                      <option key={h}>{h}</option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
          )}
        </>
      )}

      {preview && (
        <div className="preview">
          {problems.length > 0 && (
            <ul className="problems">
              {problems.map((p) => (
                <li key={p}>
                  <IconAlert />
                  <div>{p}</div>
                </li>
              ))}
            </ul>
          )}
          <p className="small muted">
            <b>{preview.count}</b> job(s) found{manual ? `; ${manual} are on sites Appli can’t fill yet (listed as Needs manual for you)` : ""}.
            {preview.count > preview.rows.length ? ` Showing the first ${preview.rows.length}.` : ""}
          </p>
          <div className="preview-table">
            <table>
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Role</th>
                  <th>Site</th>
                  <th>Link</th>
                </tr>
              </thead>
              <tbody>
                {preview.rows.slice(0, 12).map((r, i) => (
                  <tr key={i}>
                    <td>{r.company}</td>
                    <td>{r.role ?? <span className="muted">read from the posting</span>}</td>
                    <td>{r.automated ? r.ats : <span className="muted">{r.ats} (by hand)</span>}</td>
                    <td className="ellipsis">{r.url ?? <span className="muted">none</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row">
            <button className="btn primary" disabled={busy || problems.length > 0 || !preview.count} onClick={doImport}>
              {busy ? "Adding…" : `Add ${preview.count} job(s)`}
            </button>
            <button className="btn ghost" disabled={busy} onClick={() => setPreview(null)}>
              Back
            </button>
          </div>
        </div>
      )}

      {msg && (
        <div className={`banner ${msg.ok ? "ok-b" : "error"}`}>
          {msg.ok ? <IconCheck /> : <IconAlert />} {msg.text}
        </div>
      )}
    </div>
  );
}
