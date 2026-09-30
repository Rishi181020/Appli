"""Workday, one page at a time: filled, then continued to the next page; the final Submit is always the person's.

Flow for one job (the runner never clicks Submit or Sign In / Create Account):
  posting page (screened first, like every job) -> Apply -> "Autofill with Resume" (or "Apply Manually")
  -> sign in / create account: the saved Workday email + password are typed in; the person finishes the CAPTCHA
     or email check and clicks Sign In / Create Account
  -> each step is filled where the profile knows the answer. Nothing required left: the runner clicks Next / Save and
     Continue itself. Something it can't fill: status "needs_help" with the questions listed on the dashboard; answers
     saved there are typed into the page and the step continues (or the person fills it in the browser).
  -> Review: status "ready_for_review"; the person submits; the confirmation marks it submitted.
The company's own "Application Questions" are answered only from the person's saved answers, never by a model.

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
from .answering import Field, answer_fields, direct_answer, fuzzy_option, norm

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
    # the current step
    kind: str = ""
    name: str = ""
    where: str = ""
    button: str = "Save and Continue"
    help: bool = False  # waiting for your answers
    help_checked: float = 0.0


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
    """Fill what's known on this step. -> (filled count, left for the person).

    Company questions ("Application Questions" pages) are answered only from YOUR saved answers and profile, never by a
    model; everything else also uses the usual tiers (profile -> Jev -> fast model)."""
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
    left: list[dict] = []
    if kind == "questions":
        for f in rest:
            v = direct_answer(f.as_field(), profile, t.bank)
            if v:
                values[f.id] = (v, "saved")
            else:
                left.append({"id": f.id, "label": f.label, "required": f.required, "options": f.options,
                             "reason": "the company's question: answer it once and it's saved for next time"})
    else:
        res = answer_fields([f.as_field() for f in rest], profile, t.bank, t.job, t.resume_text, known=t.known)
        for fid, v in res.values.items():
            values[fid] = (v, res.tiers.get(fid, ""))
        left = [{**u, "options": u.get("options") or by_id[u["id"]].options} if u.get("id") in by_id else u
                for u in res.unanswered]
    all_by_id = {f.id: f for f in todo}
    n = 0
    for fid, (v, tier) in values.items():
        f = all_by_id[fid]
        if fill(page, f, v):
            n += 1
            t.filled.append({"label": f.label, "value": v[:400], "tier": tier or "direct"})
            if fid in by_id and kind != "questions":
                t.learn.append((f.as_field(), v, tier))
        else:
            left.append({"id": fid, "label": f.label, "reason": f"couldn't fill (wanted: {v[:80]})", "required": f.required,
                         "options": f.options})
    for f in wfields:
        if f.kind == "consent" and not f.value:
            left.append({"id": f.id, "label": f.label, "reason": "checkbox/consent: tick it in the browser",
                         "required": f.required})
    return n, left


def _fill_from_bank(t: Tracked) -> int:
    """After you answer in the dashboard: type your new saved answers into the page's still-empty fields."""
    page = t.page
    n = 0
    for f in extract(page, _SKIP_SECTIONS if t.kind == "experience" else []):
        if f.value or f.kind in ("file", "date", "consent"):
            continue
        if f.kind == "list":
            f.options = _list_options(page, f)
        v = direct_answer(f.as_field(), t.profile, t.bank)
        if v and fill(page, f, v):
            n += 1
            t.filled.append({"label": f.label, "value": v[:400], "tier": "you"})
    return n


# While the runner types into a page, a click on Next / Save and Continue / Submit is cancelled (a guard against a stray
# click or Enter). The runner itself only ever clicks Next / Save and Continue, never Submit.
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


_NEXT = f'{A("bottom-navigation-next-button")}, {A("pageFooterNextButton")}'
_ERRORS_JS = r"""
() => [...document.querySelectorAll('[data-automation-id="errorMessage"], [data-automation-id="errorBanner"] li, [role="alert"]')]
  .filter(e => e.getBoundingClientRect().height > 0).map(e => (e.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean)
"""


def _continue(t: Tracked) -> tuple[bool, list[str]]:
    """Click Next / Save and Continue (never Submit). -> (moved to another step, Workday's error messages)."""
    page = t.page
    btn = page.locator(_NEXT).first
    try:
        if not btn.count() or not btn.is_visible():
            return False, []
        if re.search(r"submit", btn.inner_text(timeout=2000) or "", re.I):
            return False, []  # the final Submit is always yours
        before = signature(page, read_step(page))
        btn.click(timeout=5000)
    except Exception:
        return False, []
    for _ in range(16):  # up to ~8s for the next step to load
        page.wait_for_timeout(500)
        if signature(page, read_step(page)) != before:
            return True, []
    try:
        errors = list(dict.fromkeys(page.evaluate(_ERRORS_JS)))[:12]
    except Exception:
        errors = []
    return False, errors


def _error_items(errors: list[str]) -> list[dict]:
    """Workday's messages ('The field X is required and must have a value.') as questions to answer."""
    out = []
    for e in errors:
        m = re.search(r"(?:the field|field)\s+(.+?)\s+is required", e, re.I)
        out.append({"id": f"err{len(out)}", "label": m.group(1).strip() if m else e[:160], "required": True,
                    "reason": f"Workday says: {e[:200]}"})
    return out


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


_AUTO_CONTINUE = ("information", "experience", "questions", "disclosures")  # pages the runner moves past itself


def _hand_over(t: Tracked, update, n: int, left: list[dict]) -> str:
    """Nothing required left: continue to the next step. Otherwise ask for help (dashboard or browser)."""
    need = [u for u in left if u.get("required")]
    if t.kind in _AUTO_CONTINUE and not need:
        moved, errors = _continue(t)
        if moved:
            print(f"    workday job {t.job['id']}: {t.where} done ({n} filled) - continued to the next step")
            update(status="filling", status_reason=f"Workday: {t.where} done, next step loading", unanswered=left,
                   filled_fields={"fields": t.filled, "uploads": t.uploads, "workday": True})
            return "moved"
        need = _error_items(errors) if errors else [{"id": "next", "label": f"Continue past {t.name}", "required": True,
                                                    "reason": "the page didn't move on: check it in the browser"}]
        left = need + [u for u in left if not u.get("required")]
    t.help = bool(need)
    if need:
        msg = (f"Workday {t.where}: {len(need)} question(s) need your answer. Answer them here (saved for next time and "
               f"typed into the page for you), or fill them in the browser and click {t.button}.")
        update(status="needs_help", status_reason=msg, unanswered=left,
               filled_fields={"fields": t.filled, "uploads": t.uploads, "workday": True})
    else:
        msg = (f"Workday {t.where}: filled {n}. Check the page, then click {t.button}." if t.kind != "other" else
               f"Workday {t.where}: this page is left for you. Fill it, then click {t.button}.")
        update(status="submission_required", status_reason=msg, unanswered=left,
               filled_fields={"fields": t.filled, "uploads": t.uploads, "workday": True})
    return "waiting"


def step(t: Tracked, update, reload_bank=None) -> str:
    """Look at the page; if it moved to a new step, fill it and (when nothing is left for you) continue to the next one.
    While a step waits for your help, answers you save on the dashboard are typed in and the step continues.
    `update(**fields)` writes the job row; `reload_bank()` returns your saved answers.
    -> 'moved' (continued to the next step: call again) | 'waiting' | 'review' | 'submitted' | 'closed'"""
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
        if t.help and reload_bank and time.time() - t.help_checked > 5:
            t.help_checked = time.time()
            bank = reload_bank()
            if bank != t.bank:  # you answered something on the dashboard
                t.bank = bank
                _guard(page, True)
                try:
                    typed = _fill_from_bank(t)
                    _, left = _fill_page(t, t.kind) if t.kind in _AUTO_CONTINUE else (0, [])
                except Exception as e:
                    print(f"    workday fill problem: {type(e).__name__}: {str(e)[:120]}")
                    typed, left = 0, [{"id": "x", "label": "page", "required": True, "reason": str(e)[:120]}]
                finally:
                    _guard(page, False)
                if typed:
                    print(f"    workday job {t.job['id']}: typed in {typed} answer(s) you gave on the dashboard")
                return _hand_over(t, update, typed, left)
        return "waiting"
    t.sig = sig
    kind = classify(s)
    if kind == "other" and re.search(r"resume|\bcv\b", s["body"], re.I) and page.locator('input[type="file"]').count():
        kind = "experience"  # "Autofill with Resume" upload page
    if kind in ("information", "experience", "questions", "disclosures", "review") and not t.signed_in:
        t.signed_in = True
        _remember_account(page.url)
    t.kind, t.name, t.help = kind, s["name"], False
    t.where = f"step {s['index']}/{s['total']} · {s['name']}" if s["index"] and s["total"] else (s["name"] or "Workday")
    t.button = s["next"] if s["next"] and len(s["next"]) < 30 else "Save and Continue"
    print(f"    workday job {t.job['id']}: {t.where} ({kind})")

    if kind == "sign_in":
        update(status="submission_required", status_reason=_sign_in(t))
        return "waiting"
    if kind == "review":
        update(status="ready_for_review", status_reason="Workday review page: check everything, then Submit",
               unanswered=[], filled_fields={"fields": t.filled, "uploads": t.uploads, "workday": True})
        return "review"

    left: list[dict] = []
    n = 0
    if kind != "other":
        try:  # the step's fields render a moment after its title
            page.wait_for_selector('[data-automation-id^="formField-"]', timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(800)
    _guard(page, True)
    try:
        if kind == "experience":
            _upload_resume(t)
        if kind != "other":
            n, left = _fill_page(t, kind)
    except Exception as e:
        print(f"    workday fill problem: {type(e).__name__}: {str(e)[:120]}")
        left = [{"id": "x", "label": "this page", "required": True, "reason": f"couldn't fill it: {str(e)[:120]}"}]
    finally:
        _guard(page, False)
    return _hand_over(t, update, n, left)
