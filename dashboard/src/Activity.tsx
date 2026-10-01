import { useEffect, useMemo, useRef, useState } from "react";
import type { Job } from "./lib";
import { IconFlame } from "./Icons";

/** Local calendar day "YYYY-MM-DD" (the heatmap and streaks count days where you are, not in UTC). */
const dayKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKS = 53;

type Day = { date: Date; key: string; count: number; future: boolean };

/** 0 = none, 1..4 = a quarter of your daily goal each (1 application counts as level 1). */
function level(count: number, goal: number): number {
  if (count <= 0) return 0;
  const g = Math.max(4, goal);
  if (count >= g * 0.75) return 4;
  if (count >= g * 0.5) return 3;
  if (count >= g * 0.25) return 2;
  return 1;
}

function streaks(active: Set<string>, today: Date) {
  // current: consecutive days up to today (or up to yesterday, while today is still open)
  let start = active.has(dayKey(today)) ? today : addDays(today, -1);
  let current = 0;
  while (active.has(dayKey(start))) {
    current++;
    start = addDays(start, -1);
  }
  // longest: the longest run of back-to-back days ever
  const toDate = (k: string) => {
    const [y, m, d] = k.split("-").map(Number);
    return new Date(y, m - 1, d);
  };
  let longest = 0;
  let run = 0;
  let prev: string | null = null;
  for (const k of [...active].sort()) {
    run = prev && dayKey(addDays(toDate(prev), 1)) === k ? run + 1 : 1;
    longest = Math.max(longest, run);
    prev = k;
  }
  return { current, longest, today: active.has(dayKey(today)) };
}

/** GitHub-style year of applications, plus streaks to keep you going. */
export default function Activity({ jobs, goal }: { jobs: Job[]; goal: number }) {
  const scroller = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);

  const { weeks, perDay, total, monthLabels } = useMemo(() => {
    const perDay = new Map<string, number>();
    for (const j of jobs) {
      if (j.status !== "submitted") continue;
      const when = new Date(j.submitted_at ?? j.updated_at);
      const k = dayKey(when);
      perDay.set(k, (perDay.get(k) ?? 0) + 1);
    }
    const today = new Date();
    const end = addDays(today, 6 - today.getDay()); // this week's Saturday
    const start = addDays(end, -(WEEKS * 7 - 1)); // a Sunday, 53 weeks back
    const weeks: Day[][] = [];
    let total = 0;
    for (let w = 0; w < WEEKS; w++) {
      const col: Day[] = [];
      for (let d = 0; d < 7; d++) {
        const date = addDays(start, w * 7 + d);
        const key = dayKey(date);
        const future = date > today;
        const count = future ? 0 : perDay.get(key) ?? 0;
        total += count;
        col.push({ date, key, count, future });
      }
      weeks.push(col);
    }
    // a month's name over the first week that starts in it
    const monthLabels = weeks.map((col, i) => {
      const m = col[0].date.getMonth();
      const starts = i === 0 || weeks[i - 1][0].date.getMonth() !== m;
      // skip a month that only gets a week or two at the left edge, so its name never overlaps the next one
      const cramped = weeks.slice(i + 1, i + 3).some((c) => c[0].date.getMonth() !== m);
      return starts && !cramped ? MONTHS[m] : "";
    });
    return { weeks, perDay, total, monthLabels };
  }, [jobs]);

  const s = useMemo(() => streaks(new Set([...perDay.entries()].filter(([, c]) => c > 0).map(([k]) => k)), new Date()), [perDay]);
  const todayCount = perDay.get(dayKey(new Date())) ?? 0;
  const thisWeek = useMemo(() => {
    const now = new Date();
    let n = 0;
    for (let i = 0; i <= now.getDay(); i++) n += perDay.get(dayKey(addDays(now, -i))) ?? 0;
    return n;
  }, [perDay]);
  const byMonth = useMemo(() => {
    const m = new Map<string, number>();
    for (const [k, c] of perDay) m.set(k.slice(0, 7), (m.get(k.slice(0, 7)) ?? 0) + c);
    return [...m.entries()].sort().reverse().slice(0, 12);
  }, [perDay]);

  useEffect(() => {
    // the latest weeks are on the right: start scrolled there on narrow screens
    if (scroller.current) scroller.current.scrollLeft = scroller.current.scrollWidth;
  }, [weeks]);

  const message = s.today
    ? todayCount >= goal
      ? `Goal met: ${todayCount} today. ${s.current > 1 ? `${s.current}-day streak and counting.` : "Nice start."}`
      : `${todayCount} today: ${goal - todayCount} more to reach your goal of ${goal}.`
    : s.current > 0
      ? `Submit one application today to keep your ${s.current}-day streak.`
      : "Submit an application today to start a streak.";
  const best = s.current > 1 && s.current >= s.longest;

  const fmt = (d: Date) => d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" });

  return (
    <section className="activity panel" aria-label="Your application activity">
      <div className="streaks">
        <div className={`streak-tile main ${s.current > 0 ? "on" : ""}`}>
          <span className="flame" aria-hidden="true">
            <IconFlame />
          </span>
          <div>
            <div className="st-value">
              {s.current} <small>day{s.current === 1 ? "" : "s"}</small>
            </div>
            <div className="st-label">Current streak{best ? " · your best" : ""}</div>
          </div>
        </div>
        <div className="streak-tile">
          <div className="st-value">
            {s.longest} <small>day{s.longest === 1 ? "" : "s"}</small>
          </div>
          <div className="st-label">Longest streak</div>
        </div>
        <div className="streak-tile">
          <div className="st-value">{thisWeek}</div>
          <div className="st-label">This week</div>
        </div>
        <div className="streak-tile">
          <div className="st-value">{total}</div>
          <div className="st-label">Past year</div>
        </div>
        <p className="streak-msg">{message}</p>
      </div>

      <div className="heat-wrap" ref={scroller} onMouseLeave={() => setTip(null)}>
        <div
          className="heat"
          role="img"
          aria-label={`${total} applications submitted in the past year; current streak ${s.current} days, longest ${s.longest} days`}
        >
          <div className="heat-months" aria-hidden="true">
            {monthLabels.map((m, i) => (
              <span key={i}>{m}</span>
            ))}
          </div>
          <div className="heat-body">
            <div className="heat-days" aria-hidden="true">
              <span />
              <span>Mon</span>
              <span />
              <span>Wed</span>
              <span />
              <span>Fri</span>
              <span />
            </div>
            <div className="heat-grid">
              {weeks.map((col, i) => (
                <div key={i} className="heat-col">
                  {col.map((d) => (
                    <span
                      key={d.key}
                      className={`heat-cell l${level(d.count, goal)} ${d.future ? "future" : ""} ${d.key === dayKey(new Date()) ? "today" : ""}`}
                      onMouseEnter={(e) => {
                        if (d.future) return setTip(null);
                        const box = (e.currentTarget.closest(".heat-wrap") as HTMLElement).getBoundingClientRect();
                        const r = e.currentTarget.getBoundingClientRect();
                        setTip({
                          x: r.left - box.left + r.width / 2 + (scroller.current?.scrollLeft ?? 0),
                          y: r.top - box.top,
                          text: `${d.count === 0 ? "No" : d.count} application${d.count === 1 ? "" : "s"} · ${fmt(d.date)}`,
                        });
                      }}
                    />
                  ))}
                </div>
              ))}
            </div>
          </div>
        </div>
        {tip && (
          <div className="heat-tip" style={{ left: tip.x, top: tip.y }} role="status">
            {tip.text}
          </div>
        )}
      </div>

      <div className="heat-foot">
        <details>
          <summary>By month</summary>
          {byMonth.length ? (
            <table className="heat-table">
              <thead>
                <tr>
                  <th>Month</th>
                  <th>Applications</th>
                </tr>
              </thead>
              <tbody>
                {byMonth.map(([m, c]) => (
                  <tr key={m}>
                    <td>{MONTHS[Number(m.slice(5)) - 1]} {m.slice(0, 4)}</td>
                    <td>{c}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="small muted">Nothing submitted yet.</p>
          )}
        </details>
        <div className="heat-legend" aria-label={`Shades: none, then quarters of your daily goal of ${goal}`}>
          <span className="small muted">Less</span>
          {[0, 1, 2, 3, 4].map((l) => (
            <span key={l} className={`heat-cell l${l}`} />
          ))}
          <span className="small muted">More</span>
        </div>
      </div>
    </section>
  );
}
