"""Workday, one page at a time, with the person clicking every "Save and Continue" and the final Submit.

Flow for one job (the runner never clicks Next / Save and Continue / Submit):
  posting page (screened first, like every job) -> Apply -> "Autofill with Resume" (or "Apply Manually")
  -> sign in / create account: the saved Workday email + password are typed in; the person finishes the CAPTCHA
     or email check and clicks Sign In / Create Account
  -> each step: filled where the profile knows the answer, then status "submission_required" until the person
     clicks Save and Continue; the next step is filled when it appears
  -> Review: status "ready_for_review"; the person submits; the confirmation marks it submitted.
The company's own "Application Questions" are left exactly as they are.

Every Workday site uses the same data-automation-id attributes, so one filler works for all of them.
"""
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page

from . import config
from .answering import Field, answer_fields, fuzzy_option, norm

# ---- the person's Workday login (one email + password for every company's Workday site) ------------------
def _creds_file() -> Path | None:
    return config.HOME / "workday.json" if config.HOME else None


def _accounts_file() -> Path | None:
    return config.HOME / "workday-accounts.json" if config.HOME else None


def credentials() -> dict:
    f = _creds_file()
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f and f.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_credentials(email: str, password: str | None):
    f = _creds_file()
    cur = credentials()
    cur["email"] = email.strip()
    if password:  # blank = keep the saved one
        cur["password"] = password
    f.write_text(json.dumps(cur), encoding="utf-8")


def _tenant(url: str) -> str:
    return urlparse(url).netloc.lower()


def _accounts() -> dict:
    f = _accounts_file()
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f and f.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _remember_account(url: str):
    """Signed in on this company's Workday once: next time go straight to 'Sign In' instead of 'Create Account'."""
    f = _accounts_file()
    if not f:
        return
    acc = _accounts()
    if _tenant(url) not in acc:
        acc[_tenant(url)] = time.strftime("%Y-%m-%d")
        f.write_text(json.dumps(acc, indent=1), encoding="utf-8")


# ---- page reading -------------------------------------------------------------------------------------------
A = lambda aid: f'[data-automation-id="{aid}"]'  # noqa: E731

_STEP_JS = r"""
() => {
  const t = el => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  // e.g. "current step 1 of 7 Autofill with Resume"
  const active = t(document.querySelector('[data-automation-id="progressBarActiveStep"]'));
  const m = active.match(/step (\d+) of (\d+)\s*(.*)$/i);
  const h = document.querySelector('[data-automation-id="applyFlowPage"] h2, main h2, h2');
  const next = document.querySelector('[data-automation-id="bottom-navigation-next-button"], [data-automation-id="pageFooterNextButton"]');
  return {
    step: m ? m[3] : active,
    index: m ? +m[1] : 0,
    total: m ? +m[2] : 0,
    heading: t(h),
    next: t(next),
    password: !!document.querySelector('input[type="password"]'),
    body: t(document.body).slice(0, 3000),
  };
}
"""

_CONFIRM = re.compile(r"application (has been |was )?submitted|thank you for (applying|your application)|successfully submitted", re.I)
_ALREADY = re.compile(r"you('ve| have) already applied|already applied (for|to) this", re.I)


def read_step(page: Page) -> dict:
    try:
        s = page.evaluate(_STEP_JS)
    except Exception:
        return {"step": "", "index": 0, "total": 0, "heading": "", "next": "", "password": False, "body": "", "name": ""}
    s["name"] = s["step"] or s["heading"]
    return s


def signature(page: Page, s: dict) -> str:
    return f"{urlparse(page.url).path}|{s['name']}|{s['password']}"


# ---- Workday's own widgets ----------------------------------------------------------------------------------
_EXTRACT_JS = r"""
(skip) => {
  const t = el => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g, ' ').trim();
  const shown = el => { const r = el.getBoundingClientRect(); return r.width > 0 || r.height > 0; };
  const out = []; let n = 0;
  document.querySelectorAll('[data-automation-id^="formField-"]').forEach(box => {
    if (!shown(box) || box.parentElement.closest('[data-automation-id^="formField-"]')) return;
    const aid = box.getAttribute('data-automation-id').slice(10);
    let section = '';
    for (let p = box.parentElement; p && !section; p = p.parentElement) {
      const h = p.querySelector(':scope > h3, :scope > h4, :scope > div > h3, :scope > div > h4');
      if (h) section = t(h);
    }
    if (skip.some(s => section.toLowerCase().includes(s))) return;
    const labelEl = box.querySelector('label, legend');
    const label = t(labelEl).replace(/\*/g, '').trim();
    const required = /\*/.test(t(labelEl)) || !!box.querySelector('[aria-required="true"], [required]');
    const id = 'w' + (n++);
    const list = box.querySelector('button[aria-haspopup="listbox"]');
    const prompt = box.querySelector('[data-automation-id="multiselectInputContainer"], input[data-uxi-widget-type="selectinput"]');
    const radios = [...box.querySelectorAll('input[type="radio"]')];
    const checks = [...box.querySelectorAll('input[type="checkbox"]')];
    const date = box.querySelector('[data-automation-id^="dateSection"], [data-automation-id="dateInputWrapper"]');
    const file = box.querySelector('input[type="file"]');
    const area = box.querySelector('textarea');
    const text = box.querySelector('input[type="text"], input[type="email"], input[type="tel"], input:not([type])');
    const labelFor = el => { const l = el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); return t(l) || t(el.closest('label')) || el.value; };
    if (file) { file.setAttribute('data-appli', id); out.push({id, aid, kind: 'file', label, required, value: '', options: []}); }
    else if (list) { list.setAttribute('data-appli', id); const v = t(list); out.push({id, aid, kind: 'list', label, required, value: /^select one$/i.test(v) ? '' : v, options: []}); }
    else if (prompt) { box.setAttribute('data-appli', id);
      const chosen = [...box.querySelectorAll('[data-automation-id="selectedItem"], [data-automation-id="selectedItemList"] li')].map(t).filter(Boolean);
      out.push({id, aid, kind: 'prompt', label, required, value: chosen.join(', '), options: []}); }
    else if (radios.length) { radios.forEach((r, k) => r.setAttribute('data-appli-opt', id + 'o' + k));
      out.push({id, aid, kind: 'radio', label, required, value: (radios.find(r => r.checked) ? labelFor(radios.find(r => r.checked)) : ''), options: radios.map(labelFor)}); }
    else if (checks.length) { checks.forEach((c, k) => c.setAttribute('data-appli-opt', id + 'o' + k));
      out.push({id, aid, kind: checks.length > 1 ? 'checkbox' : 'consent', label: label || labelFor(checks[0]), required,
                value: checks.filter(c => c.checked).map(labelFor).join(', '), options: checks.map(labelFor)}); }
    else if (date) { out.push({id, aid, kind: 'date', label, required, value: '', options: []}); }
    else if (area) { area.setAttribute('data-appli', id); out.push({id, aid, kind: 'textarea', label, required, value: area.value, options: []}); }
    else if (text) { text.setAttribute('data-appli', id); out.push({id, aid, kind: 'text', label, required, value: text.value, options: []}); }
  });
  return out;
}
"""

# Sections of "My Experience" that Workday fills from the resume and the person checks: never touched.
_SKIP_SECTIONS = ["work experience", "education", "certification", "language", "skills"]


@dataclass
class WField:
    id: str
    aid: str
    kind: str  # text | textarea | list | prompt | radio | checkbox | consent | date | file
    label: str
    required: bool
    value: str
    options: list[str] = field(default_factory=list)

    def as_field(self) -> Field:
        kind = {"list": "select", "prompt": "combobox", "consent": "consent"}.get(self.kind, self.kind)
        return Field(self.id, self.label, kind, list(self.options), self.required)


def extract(page: Page, skip_sections: list[str] | None = None) -> list[WField]:
    raw = page.evaluate(_EXTRACT_JS, skip_sections or [])
    return [WField(r["id"], r["aid"], r["kind"], re.sub(r"\s+", " ", r["label"]).strip(), r["required"], r["value"] or "",
                   [o for o in r["options"] if o]) for r in raw]


def _list_options(page: Page, f: WField) -> list[str]:
    btn = page.locator(f'[data-appli="{f.id}"]')
    try:
        btn.click(timeout=3000)
        page.wait_for_timeout(350)
        opts = [o.strip() for o in page.locator('ul[role="listbox"] [role="option"]:visible').all_inner_texts()]
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        return [o for o in opts if o and not re.match(r"^select one$", o, re.I)]
    except Exception:
        return []


def _pick_list(page: Page, f: WField, value: str) -> bool:
    btn = page.locator(f'[data-appli="{f.id}"]')
    try:
        btn.click(timeout=3000)
        page.wait_for_timeout(350)
        opts = page.locator('ul[role="listbox"] [role="option"]:visible')
        texts = [o.strip() for o in opts.all_inner_texts()]
        target = fuzzy_option(value, texts)
        if target is None:
            page.keyboard.press("Escape")
            return False
        o = opts.nth(texts.index(target))
        o.scroll_into_view_if_needed(timeout=2000)
        o.click(timeout=3000)
        page.wait_for_timeout(250)
        return True
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False


def _pick_prompt(page: Page, f: WField, value: str) -> bool:
    """Search prompts ("How did you hear about us?", school, skills): type, Enter, pick the closest result."""
    box = page.locator(f'[data-appli="{f.id}"]')
    try:
        inp = box.locator("input").first
        inp.click(timeout=3000)
        inp.fill(value[:60])
        inp.press("Enter")
        page.wait_for_timeout(900)
        opts = page.locator('[data-automation-id="promptOption"]:visible, [role="option"]:visible')
        texts = [o.strip() for o in opts.all_inner_texts()]
        target = fuzzy_option(value, texts)
        if target is None:
            inp.fill("")
            page.keyboard.press("Escape")
            return False
        opts.nth(texts.index(target)).click(timeout=3000)
        page.wait_for_timeout(300)
        page.keyboard.press("Escape")
        return True
    except Exception:
        return False


def fill(page: Page, f: WField, value: str) -> bool:
    from . import forms

    if f.kind == "list":
        return _pick_list(page, f, value)
    if f.kind == "prompt":
        return _pick_prompt(page, f, value)
    if f.kind in ("text", "textarea", "radio", "checkbox"):
        return forms.fill_field(page, f.as_field(), value)
    return False


# ---- extra direct answers only Workday asks -------------------------------------------------------------------
def _workday_direct(f: WField, profile) -> str | None:
    l = f.label.lower()
    if re.search(r"phone device type|phone type", l):
        return fuzzy_option("Mobile", f.options) or "Mobile"
    if re.search(r"country phone code|phone (country )?code", l):
        from .places import country_option

        return country_option(profile.country, f.options) if f.options else None
    if re.search(r"^phone number$", l) and profile.phone:
        return re.sub(r"^\+?1[\s-]*", "", profile.phone).strip()  # the country code has its own box
    return None


# ---- one tracked application ---------------------------------------------------------------------------------
@dataclass
class Tracked:
    job: dict
    page: Page
    profile: object
    bank: dict
    known: list | None
    resume_path: Path
    resume_key: str
    resume_text: str
    sig: str = ""
    filled: list = field(default_factory=list)
    uploads: list = field(default_factory=list)
    learn: list = field(default_factory=list)
    signed_in: bool = False


def begin(page: Page, job: dict) -> str | None:
    """From the posting page to the start of the application. -> None, or a reason it can't continue."""
    s = read_step(page)
    if _ALREADY.search(s["body"]):
        return "already applied"
    apply_btn = page.locator(A("adventureButton")).first
    try:
        if apply_btn.count():
            apply_btn.click(timeout=8000)
        else:
            page.get_by_role("button", name=re.compile(r"^\s*apply\s*$", re.I)).first.click(timeout=8000)
    except Exception:
        return "Couldn't find Workday's Apply button"
    page.wait_for_timeout(1500)
    # the "how do you want to apply" choice; the resume fills most of My Experience for the person to check
    for aid in ("autofillWithResume", "applyManually"):
        opt = page.locator(A(aid)).first
        try:
            if opt.count() and opt.is_visible():
                opt.click(timeout=5000)
                page.wait_for_timeout(1500)
                break
        except Exception:
            continue
    return None


def _sign_in(t: Tracked) -> str:
    """Type the saved Workday email + password. The person finishes the CAPTCHA / email check and clicks the button."""
    page = t.page
    creds = credentials()
    known_tenant = _tenant(page.url) in _accounts()
    if known_tenant:  # we've got an account here: switch from 'Create Account' to 'Sign In'
        try:
            link = page.locator(f'{A("signInLink")}, button:has-text("Sign In"), a:has-text("Sign In")').first
            if link.count() and link.is_visible() and not page.locator(A("signInSubmitButton")).count():
                link.click(timeout=3000)
                page.wait_for_timeout(1200)
        except Exception:
            pass
    if not creds.get("email") or not creds.get("password"):
        return ("Workday sign-in: add your Workday email and password on My files to have them typed in. For now, sign in or "
                "create the account yourself, then continue.")
    typed = 0
    for aid, value in (("email", creds["email"]), ("password", creds["password"]), ("verifyPassword", creds["password"])):
        box = page.locator(f'input{A(aid)}:visible').first
        try:
            if box.count() and not box.input_value():
                box.fill(value, timeout=3000)
                typed += 1
        except Exception:
            continue
    if not typed:  # older layouts without automation ids
        try:
            page.locator('input[type="email"]:visible, input[name*="mail" i]:visible').first.fill(creds["email"], timeout=3000)
            for pw in page.locator('input[type="password"]:visible').all():
                pw.fill(creds["password"], timeout=3000)
        except Exception:
            pass
    what = "Sign in" if known_tenant else "Create your account (or use Sign In if you already have one here)"
    return (f"Workday: {what}. Your email and password are typed in; tick the terms box if there is one, finish any CAPTCHA "
            "or email verification, then click the button.")


def _upload_resume(t: Tracked) -> bool:
    page = t.page
    if t.resume_path.name.lower() in page.inner_text("body").lower():
        return False  # already attached
    inp = page.locator('input[type="file"]').first  # the Resume/CV slot comes first on Workday's pages
    try:
        if not inp.count():
            return False
        inp.set_input_files(str(t.resume_path), timeout=8000)
        page.wait_for_timeout(2500)  # Workday uploads and parses it right away
        t.uploads.append(f"resume ({t.resume_key})")
        return True
    except Exception:
        return False


def _fill_page(t: Tracked, kind: str) -> tuple[int, list[dict]]:
    """Fill what the profile knows on this step. -> (filled count, left for the person)."""
    page, profile = t.page, t.profile
    skip = _SKIP_SECTIONS if kind == "experience" else []
    wfields = [f for f in extract(page, skip) if f.kind not in ("file", "date")]
    if kind == "experience":  # only the links (LinkedIn, GitHub, website); the rest comes from the resume
        wfields = [f for f in wfields if re.search(r"linkedin|github|website|portfolio|url", f.label, re.I)]
    todo = [f for f in wfields if not f.value and f.kind != "consent"]
    for f in todo:
        if f.kind == "list":
            f.options = _list_options(page, f)
    values: dict[str, tuple[str, str]] = {}
    rest = []
    for f in todo:
        v = _workday_direct(f, profile)
        if v:
            values[f.id] = (v, "direct")
        else:
            rest.append(f)
    by_id = {f.id: f for f in rest}
    res = answer_fields([f.as_field() for f in rest], profile, t.bank, t.job, t.resume_text, known=t.known)
    for fid, v in res.values.items():
        values[fid] = (v, res.tiers.get(fid, ""))
    left = [u for u in res.unanswered]
    all_by_id = {f.id: f for f in todo}
    n = 0
    for fid, (v, tier) in values.items():
        f = all_by_id[fid]
        if fill(page, f, v):
            n += 1
            t.filled.append({"label": f.label, "value": v[:400], "tier": tier or "direct"})
            if fid in by_id:
                t.learn.append((f.as_field(), v, tier))
        else:
            left.append({"id": fid, "label": f.label, "reason": f"couldn't fill (wanted: {v[:80]})", "required": f.required,
                         "options": f.options})
    for f in wfields:
        if f.kind == "consent" and not f.value:
            left.append({"id": f.id, "label": f.label, "reason": "checkbox/consent left for you", "required": f.required})
    return n, left


# While the runner types into a page, a click on Next / Save and Continue / Submit is cancelled (it never clicks them
# itself; this guards against a stray click or Enter). Lifted as soon as the page is handed to the person.
_NEXT_GUARD_JS = """
(on) => {
  if (!window.__appliNextGuard) {
    window.__appliNextGuard = true;
    const stop = e => {
      if (window.__appliFilling && e.target && e.target.closest &&
          e.target.closest('[data-automation-id="bottom-navigation-next-button"], [data-automation-id="pageFooterNextButton"], [data-automation-id*="submit" i]')) {
        e.preventDefault(); e.stopImmediatePropagation();
      }
    };
    document.addEventListener('click', stop, true);
    document.addEventListener('keydown', e => { if (e.key === 'Enter') stop(e); }, true);
  }
  window.__appliFilling = on;
}
"""


def _guard(page: Page, on: bool):
    try:
        page.evaluate(_NEXT_GUARD_JS, on)
    except Exception:
        pass


def classify(s: dict) -> str:
    name = s["name"].lower()
    if s["password"]:
        return "sign_in"
    if re.search(r"review", name) or re.search(r"^\s*submit\s*$", s["next"], re.I):
        return "review"
    if re.search(r"question", name):
        return "questions"
    if re.search(r"my information|contact information|personal information", name):
        return "information"
    if re.search(r"experience|resume|autofill", name):
        return "experience"
    if re.search(r"voluntary|disclosure|self[- ]?identif|disabilit|veteran|eeo", name):
        return "disclosures"
    return "other"


def step(t: Tracked, update) -> str:
    """Look at the page; if it moved to a new step, fill it. `update(**fields)` writes the job row.
    -> 'waiting' | 'review' | 'submitted' | 'closed'"""
    page = t.page
    try:
        if page.is_closed():
            return "closed"
    except Exception:
        return "closed"
    s = read_step(page)
    if _CONFIRM.search(s["body"]) and not s["password"]:
        return "submitted"
    sig = signature(page, s)
    if sig == t.sig:
        return "waiting"
    t.sig = sig
    kind = classify(s)
    if kind == "other" and re.search(r"resume|\bcv\b", s["body"], re.I) and page.locator('input[type="file"]').count():
        kind = "experience"  # "Autofill with Resume" upload page
    if kind in ("information", "experience", "questions", "disclosures", "review") and not t.signed_in:
        t.signed_in = True
        _remember_account(page.url)
    where = f"step {s['index']}/{s['total']} · {s['name']}" if s["index"] and s["total"] else (s["name"] or "Workday")
    print(f"    workday job {t.job['id']}: {where} ({kind})")

    if kind == "sign_in":
        reason = _sign_in(t)
        update(status="submission_required", status_reason=reason)
        return "waiting"
    if kind == "review":
        update(status="ready_for_review", status_reason="Workday review page: check everything, then Submit",
               filled_fields={"fields": t.filled, "uploads": t.uploads, "workday": True})
        return "review"

    left: list[dict] = []
    n = 0
    button = s["next"] if s["next"] and len(s["next"]) < 30 else "Save and Continue"
    if kind not in ("questions", "other"):
        try:  # the step's fields render a moment after its title
            page.wait_for_selector('[data-automation-id^="formField-"]', timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(800)
    _guard(page, True)
    try:
        if kind == "experience":
            _upload_resume(t)
        if kind not in ("questions", "other"):
            n, left = _fill_page(t, kind)
    except Exception as e:
        print(f"    workday fill problem: {type(e).__name__}: {str(e)[:120]}")
    finally:
        _guard(page, False)
    if kind == "questions":
        msg = f"Workday {where}: the company's own questions are left for you. Answer them, then click {button}."
    else:
        need = [u for u in left if u.get("required")]
        did = [f"filled {n}"] + (["resume attached"] if kind == "experience" and t.uploads else [])
        msg = (f"Workday {where}: {', '.join(did)}" + (f", {len(need)} required left for you" if need else "")
               + f". Check the page, then click {button}.")
        if kind == "other":
            msg = f"Workday {where}: this page is left for you. Fill it, then click {button}."
    update(status="submission_required", status_reason=msg, unanswered=left,
           filled_fields={"fields": t.filled, "uploads": t.uploads, "workday": True})
    return "waiting"
