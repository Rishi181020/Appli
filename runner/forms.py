"""Generic application-form extraction and filling for Playwright pages.

extract() tags every fillable control with a data-appli attribute and returns Field objects;
fill() applies answers by type. Nothing here ever clicks a submit button.
"""
import difflib
import re

from playwright.sync_api import Page

from .answering import Field, fuzzy_option, match_option, norm

_EXTRACT_JS = r"""
() => {
  const txt = el => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  const shown = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && (r.width > 0 || r.height > 0); };
  const labelOf = el => {
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l && txt(l)) return txt(l); }
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(' ').map(i => txt(document.getElementById(i))).join(' ').trim(); if (t) return t; }
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const wrap = el.closest('label');
    if (wrap) { const c = wrap.cloneNode(true); c.querySelectorAll('input,select,textarea').forEach(x => x.remove()); if (txt(c)) return txt(c); }
    let p = el.parentElement;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      const l = p.querySelector(':scope > label, :scope > legend, :scope > [class*="label" i]');
      if (l && !l.contains(el) && txt(l)) return txt(l);
    }
    return el.placeholder || el.name || '';
  };
  const groupLabel = (inputs, optLabels) => {
    const fs = inputs[0].closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg && txt(lg)) return txt(lg); }
    let p = inputs[0].parentElement;
    for (let i = 0; i < 7 && p; i++, p = p.parentElement) {
      if (!inputs.every(x => p.contains(x))) continue;
      const cands = [...p.querySelectorAll('label,legend,p,h3,h4,h5,span,div')].filter(e =>
        !e.querySelector('input,select,textarea') && txt(e) && txt(e).length < 400 &&
        !optLabels.includes(txt(e)) &&
        (e.compareDocumentPosition(inputs[0]) & Node.DOCUMENT_POSITION_FOLLOWING));
      if (cands.length) return txt(cands[cands.length - 1]);
    }
    return inputs[0].name || '';
  };
  const isReq = (el, label) => el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label);
  const out = []; let n = 0; const groups = {};
  document.querySelectorAll('input,textarea,select').forEach(el => {
    const t = (el.type || el.tagName).toLowerCase();
    if (['hidden','submit','button','image','reset','search'].includes(t) || el.disabled) return;
    if (el.closest('[aria-hidden="true"]') && t !== 'file') return;
    if (t !== 'file' && !shown(el) && el.getAttribute('role') !== 'combobox') return;
    if (el.getAttribute('role') === 'combobox' || el.closest('[class*="select__control"]')) {
      const label = labelOf(el); const id = 'f' + (n++); el.setAttribute('data-appli', id);
      out.push({id, type: 'combobox', label, options: [], required: isReq(el, label)}); return;
    }
    if (t === 'radio' || t === 'checkbox') {
      const key = t + ':' + (el.name || el.id);
      (groups[key] = groups[key] || {type: t, els: []}).els.push(el); return;
    }
    let label = labelOf(el); const id = 'f' + (n++); el.setAttribute('data-appli', id);
    if (t === 'file' && !/resume|\bcv\b|cover letter|transcript/i.test(label)) {
      let a = el.parentElement;
      for (let i = 0; i < 7 && a; i++, a = a.parentElement) {
        const tx = txt(a);
        if (tx.length < 220 && /resume|\bcv\b|cover letter|transcript/i.test(tx)) { label = tx; break; }
      }
    }
    let type = t === 'select-one' || t === 'select-multiple' ? 'select' : t === 'textarea' ? 'textarea' : t;
    if (!['textarea','select','file','email','tel','url','number'].includes(type)) type = 'text';
    const options = type === 'select' ? [...el.options].map(o => txt(o)).filter(s => s && !/^(select|choose|please)/i.test(s)) : [];
    out.push({id, type, label, options, required: isReq(el, label)});
  });
  Object.values(groups).forEach(g => {
    const optLabels = g.els.map(e => labelOf(e));
    const label = groupLabel(g.els, optLabels); const id = 'f' + (n++);
    g.els.forEach((e, k) => e.setAttribute('data-appli-opt', id + 'o' + k));
    out.push({id, type: g.type === 'radio' || g.els.length > 1 ? (g.type) : 'consent', label, options: optLabels,
              required: g.els.some(e => isReq(e, label))});
  });
  // Ashby-style Yes/No button pairs
  document.querySelectorAll('div,fieldset').forEach(c => {
    const kids = [...c.children];
    if (kids.length === 2 && kids.every(k => k.tagName === 'BUTTON') &&
        kids.map(k => txt(k).toLowerCase()).sort().join() === 'no,yes' && !c.hasAttribute('data-appli')) {
      const id = 'f' + (n++); c.setAttribute('data-appli', id);
      const label = labelOf(kids[0]) !== txt(kids[0]) ? labelOf(kids[0]) : groupLabel([kids[0]], ['Yes', 'No']);
      out.push({id, type: 'buttons', label, options: ['Yes', 'No'], required: /\*\s*$/.test(label)});
    }
  });
  return out;
}
"""


def extract(page: Page) -> list[Field]:
    """Tag and describe every fillable control. Combobox options are discovered separately (discover_options)."""
    raw = page.evaluate(_EXTRACT_JS)
    return [
        Field(r["id"], re.sub(r"\s*[*✱]+\s*", " ", r["label"]).strip(), r["type"], r["options"], r["required"])
        for r in raw
    ]


def discover_options(page: Page, fields: list[Field]) -> None:
    """Open each combobox once to learn its options (small lists only)."""
    for f in fields:
        if f.type == "combobox" and not f.options:
            f.options = _combobox_options(page, f.id)


def _menu_options(page: Page, loc):
    """Options locator scoped to the menu belonging to this combobox (not any other open menu)."""
    lid = loc.get_attribute("aria-controls")
    if lid:
        return page.locator(f'[id="{lid}"] [role="option"]')
    return loc.locator('xpath=ancestor::div[contains(@class,"select")][1]').locator('[role="option"]')


def _combobox_options(page: Page, fid: str) -> list[str]:
    """Options of a small fixed list. Big/searchable lists (country, school...) return [] and are typed into."""
    loc = page.locator(f'[data-appli="{fid}"]')
    try:
        loc.click(timeout=2000)
        page.wait_for_timeout(300)
        opts = [t.strip() for t in _menu_options(page, loc).all_inner_texts() if t.strip()]
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        return [] if len(opts) > 40 else opts
    except Exception:
        return []


_SCHOOL_LABEL = re.compile(r"school|university|college|institution", re.I)


def _school_target(value: str, texts: list[str]) -> str | None:
    """Only a confident match: a wrong school is worse than 'Other'."""
    v = norm(value)
    for t in texts:
        if norm(t) == v:
            return t
    for t in texts:
        n = norm(t)
        if v and v in n:  # option spells out the whole name (plus e.g. a city)
            return t
        # never accept an option that is merely a piece of the value: that is a different, shorter-named school
    close = difflib.get_close_matches(v, [norm(t) for t in texts], n=1, cutoff=0.9)
    return next((t for t in texts if norm(t) == close[0]), None) if close else None


def _pick_other(page: Page, loc) -> bool:
    loc.fill("")
    loc.fill("Other")
    page.wait_for_timeout(600)
    opts = _menu_options(page, loc)
    texts = [t.strip() for t in opts.all_inner_texts()]
    for i, t in enumerate(texts):
        if norm(t) == "other":
            opts.nth(i).click()
            return True
    for i, t in enumerate(texts):
        if norm(t).startswith("other"):
            opts.nth(i).click()
            return True
    return False


def _click_combobox(page: Page, loc, value: str, school: bool = False):
    """Type into a react-select style box and pick the best matching option, retrying with shorter searches.
    Schools are matched strictly, and fall back to the 'Other' option when the list doesn't have them."""
    queries = []
    words = value.split()
    shorter = (value, " ".join(words[:3])) if school else (value, re.split(r"\s+(?:and|&|/)\s+", value)[0], " ".join(words[:2]), words[0])
    for q in shorter:
        q = q.strip()[:60]
        if q and q not in queries:
            queries.append(q)
    loc.click(timeout=3000)
    for q in queries:
        loc.fill("")
        loc.fill(q)
        page.wait_for_timeout(600)
        opts = _menu_options(page, loc)
        texts = [t.strip() for t in opts.all_inner_texts()]
        if not texts:
            continue
        target = None
        if school:
            target = _school_target(value, texts)
        else:
            if re.search(r",\s*[A-Z]{2}\b", value):  # "Santa Clara, CA": prefer the US match over e.g. Cuba
                us = [t for t in texts if norm(value.split(",")[0]) in norm(t) and re.search(r"\b(CA|California|United States|USA)\b", t)]
                target = us[0] if us else None
            target = target or fuzzy_option(value, texts)
            if target is None and q != value:
                target = fuzzy_option(q, texts)
        if target is not None:
            opts.nth(texts.index(target)).click()
            return
    if school and _pick_other(page, loc):
        return
    page.keyboard.press("Escape")
    raise ValueError(f"no option matching {value!r}")


def is_school_field(f: Field) -> bool:
    """The 'School' box of an education block (used to count how many blocks the form currently has)."""
    return f.type in ("combobox", "select", "text") and bool(re.match(r"^(school|university|college|institution)( name)?$", f.label.strip().lower()))


def click_add_education(page: Page) -> bool:
    """Press the form's 'Add another' / 'Add education' button (Greenhouse and others) to open one more block."""
    btn = page.get_by_role("button", name=re.compile(r"^\s*add (another|education|school)", re.I))
    try:
        if btn.count() and btn.first.is_visible():
            btn.first.click(timeout=3000)
            return True
    except Exception:
        pass
    return False


def fill_field(page: Page, f: Field, value: str) -> bool:
    """Fill one field. Returns False (without raising) if it could not be filled."""
    try:
        loc = page.locator(f'[data-appli="{f.id}"]')
        if f.type in ("text", "textarea", "email", "tel", "url", "number"):
            loc.fill(value, timeout=4000)
        elif f.type == "select":
            if _SCHOOL_LABEL.search(f.label):
                pick = _school_target(value, f.options) or next((o for o in f.options if norm(o) == "other"), None)
                loc.select_option(label=pick or value, timeout=4000)
            else:
                loc.select_option(label=fuzzy_option(value, f.options) or value, timeout=4000)
        elif f.type == "combobox":
            _click_combobox(page, loc, value, school=bool(_SCHOOL_LABEL.search(f.label)))
        elif f.type == "buttons":
            loc.locator("button", has_text=re.compile(rf"^\s*{re.escape(value)}\s*$", re.I)).click(timeout=3000)
        elif f.type in ("radio", "checkbox"):
            wanted = [v.strip() for v in value.split(",")] if f.type == "checkbox" else [value]
            for w in wanted:
                opt = match_option(w, f.options)
                if opt is None:
                    return False
                k = f.options.index(opt)
                el = page.locator(f'[data-appli-opt="{f.id}o{k}"]')
                el.check(timeout=3000, force=True)
        else:
            return False
        return True
    except Exception:
        return False


def upload(page: Page, f: Field, path: str) -> bool:
    try:
        page.locator(f'[data-appli="{f.id}"]').set_input_files(path, timeout=8000)
        return True
    except Exception:
        return False


_COVER = re.compile(r"cover letter", re.I)
_RESUME = re.compile(r"resume|\bcv\b|curriculum", re.I)


def kind_of_file(f: Field) -> str | None:
    """'resume' | 'cover' | None for a file input."""
    if f.type != "file":
        return None
    if _COVER.search(f.label):
        return "cover"
    if _RESUME.search(f.label):
        return "resume"
    return None
