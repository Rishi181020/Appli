// Mirror of runner/profile_md.py render(): structured profile -> profile.md (what every model call reads).
// Keep the two in sync; the runner re-renders from the same data, so the Review step shows exactly this text.

export type Education = { school: string; degree: string; field: string; start: string; end: string; gpa: string };
export type Experience = { title: string; company: string; start: string; end: string; bullets: string[] };
export type SkillGroup = { group: string; items: string };
export type ProfileData = {
  name: string;
  email: string;
  phone: string;
  location: string;
  country: string;
  state: string;
  linkedin: string;
  github: string;
  website: string;
  education: Education[];
  experience: Experience[];
  skills: SkillGroup[];
  authorization: Record<string, string>;
  common: Record<string, string>;
  notes: string;
};

export const AUTH_QUESTIONS: { q: string; options: string[]; required?: boolean; hint?: string }[] = [
  { q: "Authorized to work in the US?", options: ["Yes", "No"], required: true },
  {
    q: "Will you now or in the future require sponsorship for employment visa status?",
    options: ["Yes", "No"],
    required: true,
    hint: "Yes = postings that say “no sponsorship” are skipped for you.",
  },
  {
    q: "Are you a US citizen or permanent resident?",
    options: ["Yes", "No"],
    hint: "No = “US citizens / US persons only” postings are skipped.",
  },
  {
    q: "Can you obtain a US security clearance?",
    options: ["Yes", "No"],
    hint: "No = clearance-required postings are skipped.",
  },
  { q: "Disability?", options: ["No", "Yes", "Prefer not to say"] },
  { q: "Gender", options: ["Male", "Female", "Non-binary", "Prefer not to say"] },
  { q: "Pronouns", options: ["He/Him", "She/Her", "They/Them", "Prefer not to say"] },
  {
    q: "Race",
    options: [
      "Asian",
      "Black or African American",
      "Hispanic or Latino",
      "White",
      "Native American or Alaska Native",
      "Native Hawaiian or Pacific Islander",
      "Two or more races",
      "Prefer not to say",
    ],
  },
  { q: "Hispanic or Latino?", options: ["No", "Yes", "Prefer not to say"] },
  { q: "Veteran?", options: ["No", "Yes", "Prefer not to say"] },
  { q: "LGBTQ+?", options: ["No", "Yes", "Prefer not to say"] },
  { q: "Sexual orientation", options: ["Heterosexual", "Gay or Lesbian", "Bisexual", "Prefer not to say"] },
];

export const COMMON: { key: string; label: string; rule: string; placeholder: string }[] = [
  { key: "start_date", label: "Earliest start date", rule: "~start date|when are you able to|when would you be|earliest you can|available to start", placeholder: "June 2027" },
  { key: "salary", label: "Salary expectation", rule: "~compensation|salary|pay expectation", placeholder: "Open to discuss" },
  { key: "relocation", label: "Willing to relocate", rule: "~relocate|relocation", placeholder: "Yes" },
  { key: "work_arrangement", label: "Work arrangement preference", rule: "~work arrangement|remote|hybrid|onsite preference", placeholder: "Open to onsite, hybrid or remote" },
  { key: "notice_period", label: "Notice period", rule: "~notice period", placeholder: "2 weeks" },
  { key: "hear_about", label: "How did you hear about us", rule: "~how did you hear|referred by|referral source", placeholder: "LinkedIn" },
];

export const emptyProfile = (): ProfileData => ({
  name: "",
  email: "",
  phone: "",
  location: "",
  country: "",
  state: "",
  linkedin: "",
  github: "",
  website: "",
  education: [],
  experience: [],
  skills: [],
  authorization: {},
  common: {},
  notes: "",
});

const clean = (s: unknown) => String(s ?? "").replace(/\s+/g, " ").trim();
const dates = (a: string, b: string) => `${clean(a) || "?"} to ${clean(b) || "Present"}`;

export function renderProfile(d: ProfileData): string {
  const out: string[] = [`# ${clean(d.name)}`, ""];
  out.push([clean(d.location), clean(d.email), clean(d.phone)].filter(Boolean).join(" · "));
  for (const [label, key] of [
    ["Country", "country"],
    ["State/Province", "state"],
    ["LinkedIn", "linkedin"],
    ["GitHub", "github"],
    ["Website", "website"],
  ] as const) {
    if (clean(d[key])) out.push(`${label}: ${clean(d[key])}`);
  }
  out.push("");

  if (d.education.length) {
    out.push("## Education", "");
    for (const e of d.education) {
      let head = `**${clean(e.school)}** — ${clean(e.degree)}`;
      if (clean(e.field)) head += `, ${clean(e.field)}`;
      const line = dates(e.start, e.end) + (clean(e.gpa) ? ` · GPA ${clean(e.gpa)}` : "");
      out.push(head, line, "");
    }
  }

  if (d.experience.length) {
    out.push("## Work Experience", "");
    for (const x of d.experience) {
      out.push(`### ${clean(x.title)} — ${clean(x.company)}`, "", dates(x.start, x.end), "");
      const bullets = (x.bullets ?? []).filter((b) => clean(b));
      bullets.forEach((b) => out.push(`- ${clean(b)}`));
      if (bullets.length) out.push("");
    }
  }

  const skills = d.skills.filter((s) => clean(s.items));
  if (skills.length) {
    out.push("## Skills", "", ...skills.map((s) => `- **${clean(s.group) || "Skills"}:** ${clean(s.items)}`), "");
  }

  const known = AUTH_QUESTIONS.map((a) => a.q);
  const authRows = [
    ...known.filter((q) => clean(d.authorization[q])).map((q) => [q, clean(d.authorization[q])]),
    ...Object.entries(d.authorization)
      .filter(([q, a]) => !known.includes(q) && clean(a))
      .map(([q, a]) => [q, clean(a)]),
  ];
  if (authRows.length) {
    out.push("## Equal Employment and Work Authorization", "", "| Question | Answer |", "|---|---|");
    authRows.forEach(([q, a]) => out.push(`| ${q} | ${a} |`));
    out.push("");
  }

  const commonRows = COMMON.filter((c) => clean(d.common[c.key])).map((c) => [c.label, clean(d.common[c.key])]);
  if (commonRows.length) {
    out.push("## Common Application Answers", "", "| Question | Answer |", "|---|---|");
    commonRows.forEach(([q, a]) => out.push(`| ${q} | ${a} |`));
    out.push("");
  }

  if (clean(d.notes)) out.push("## Additional Notes", "", d.notes.trim(), "");
  return out.join("\n").replace(/\s+$/, "") + "\n";
}

export function validateProfile(d: ProfileData): string[] {
  const errors: string[] = [];
  if (!clean(d.name)) errors.push("Name is required");
  if (!/^[\w.+-]+@[\w-]+\.[\w.]+$/.test(clean(d.email))) errors.push("A valid email is required");
  if (clean(d.phone) && !/^\+\d[\d ()-]{8,}\d$/.test(clean(d.phone))) errors.push("Phone must include the country code, e.g. +1 408 555 0123");
  const country = clean(d.country).toLowerCase();
  if (!country) errors.push("Country you live in is required");
  else if (US_NAMES.includes(country) && !clean(d.state)) errors.push("State is required for the United States");
  if (!d.education.length) errors.push("Add at least one education entry");
  d.education.forEach((e, i) => {
    if (!clean(e.school) || !clean(e.degree)) errors.push(`Education ${i + 1}: school and degree are required`);
  });
  AUTH_QUESTIONS.filter((a) => a.required).forEach((a) => {
    if (!clean(d.authorization[a.q])) errors.push(`Answer: ${a.q}`);
  });
  for (const key of ["linkedin", "github", "website"] as const) {
    if (clean(d[key]) && !/^https?:\/\//.test(clean(d[key]))) errors.push(`${key} must be a full URL (https://…)`);
  }
  return errors;
}

/** Fill any missing arrays/objects so older or imported data always has the full shape. */
export function normalizeProfile(raw: Partial<ProfileData> | null | undefined): ProfileData {
  const base = emptyProfile();
  const d = { ...base, ...(raw ?? {}) } as ProfileData;
  const eduBlank: Education = { school: "", degree: "", field: "", start: "", end: "", gpa: "" };
  const expBlank: Experience = { title: "", company: "", start: "", end: "", bullets: [] };
  const skillBlank: SkillGroup = { group: "", items: "" };
  d.education = ((d.education ?? []) as Partial<Education>[]).map((e) => ({ ...eduBlank, ...e }));
  d.experience = ((d.experience ?? []) as Partial<Experience>[]).map((x) => ({ ...expBlank, ...x, bullets: x.bullets ?? [] }));
  d.skills = ((d.skills ?? []) as Partial<SkillGroup>[]).map((s) => ({ ...skillBlank, ...s }));
  d.authorization = d.authorization ?? {};
  d.common = d.common ?? {};
  if (!d.country || !d.state) {
    // older profiles and resume imports: "Santa Clara, CA" means California, United States
    const m = /,\s*([A-Z]{2})\b/.exec(d.location ?? "");
    if (m && US_STATES[m[1]]) {
      d.state = d.state || US_STATES[m[1]];
      d.country = d.country || "United States";
    }
  }
  return d;
}

export const US_NAMES = ["united states", "united states of america", "usa", "us"];
export const US_STATES: Record<string, string> = {
  AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado", CT: "Connecticut",
  DE: "Delaware", DC: "District of Columbia", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois",
  IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland",
  MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana",
  NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York",
  NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania",
  RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah",
  VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
  PR: "Puerto Rico",
};
export const COUNTRIES = [
  "United States", "Canada", "India", "United Kingdom", "Germany", "France", "Netherlands", "Ireland", "Singapore",
  "Australia", "China", "Japan", "South Korea", "Brazil", "Mexico", "Israel", "United Arab Emirates", "Other",
];
