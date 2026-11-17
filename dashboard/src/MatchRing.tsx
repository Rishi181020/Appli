import { BAND_LABEL, Breakdown, matchBand, pctText } from "./lib";

const PARTS: [keyof Breakdown["parts"], string][] = [["skills", "Skills"], ["seniority", "Experience level"], ["education", "Education"], ["industry", "Industry"]];

/** The overall match as a ring, colored by band, with the four parts on hover. */
export default function MatchRing({ pct, breakdown, size = 40, label = false }: { pct: number | null | undefined; breakdown?: Breakdown | null; size?: number; label?: boolean }) {
  const band = matchBand(pct);
  const r = size / 2 - 3;
  const c = 2 * Math.PI * r;
  const title = breakdown ? PARTS.map(([k, name]) => `${name}: ${pctText(breakdown.parts[k])}`).join("\n") : BAND_LABEL[band];
  return (
    <span className={`match-ring band-${band}`} title={title}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} className="track" />
        <circle cx={size / 2} cy={size / 2} r={r} className="value" strokeDasharray={`${c * (pct ?? 0)} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
        <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle">{pct == null ? "–" : Math.round(pct * 100)}</text>
      </svg>
      {label && <span className="band-label">{BAND_LABEL[band]}</span>}
    </span>
  );
}

/** The four parts as small bars (the studio's score card). */
export function MatchParts({ breakdown }: { breakdown: Breakdown }) {
  return (
    <div className="match-parts">
      {PARTS.map(([k, name]) => (
        <div key={k} className="match-part">
          <span>{name}</span>
          <span className="bar"><span className={`fill band-${matchBand(breakdown.parts[k])}`} style={{ width: `${Math.round(breakdown.parts[k] * 100)}%` }} /></span>
          <b>{pctText(breakdown.parts[k])}</b>
        </div>
      ))}
    </div>
  );
}
