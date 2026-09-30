import { useEffect, useRef, useState } from "react";
import { IconChevron } from "./Icons";

export type Option = { value: string; label: string; count?: number };

/** A dropdown of checkboxes. Nothing ticked = no filter ("All …"). */
export default function MultiSelect({
  label,
  allLabel,
  options,
  value,
  onChange,
}: {
  label: string; // what one item is called, for the summary: "site" -> "2 sites"
  allLabel: string; // shown when nothing is ticked: "All sites"
  options: Option[];
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const picked = options.filter((o) => value.includes(o.value));
  const summary =
    picked.length === 0 ? allLabel : picked.length === 1 ? picked[0].label : `${picked.length} ${label}${label.endsWith("s") ? "" : "s"}`;
  const toggle = (v: string) => onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);

  return (
    <div className={`ms ${picked.length ? "active" : ""}`} ref={ref}>
      <button type="button" className="ms-btn" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="ms-summary">{summary}</span>
        {picked.length > 0 && <span className="ms-count">{picked.length}</span>}
        <span className={`ms-caret ${open ? "up" : ""}`}>
          <IconChevron />
        </span>
      </button>
      {open && (
        <div className="ms-pop" role="listbox" aria-multiselectable="true" aria-label={allLabel}>
          {options.map((o) => (
            <label key={o.value} className="ms-opt">
              <input type="checkbox" checked={value.includes(o.value)} onChange={() => toggle(o.value)} />
              <span>{o.label}</span>
              {o.count !== undefined && <em>{o.count}</em>}
            </label>
          ))}
          <div className="ms-foot">
            <button type="button" className="btn ghost small-btn" disabled={!value.length} onClick={() => onChange([])}>
              Clear
            </button>
            <button type="button" className="btn small-btn" onClick={() => setOpen(false)}>
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
