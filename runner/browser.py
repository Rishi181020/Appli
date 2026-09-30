"""Headed Playwright context with a hard submit guard.

While the agent is driving a page, two independent layers stop any application from being sent:
  1. a network guard that aborts POST/PUT/PATCH requests that look like application submissions;
  2. a DOM guard (capture-phase 'submit' listener) that cancels form submission.
Both are lifted per page by release() once the form is filled, so Rishi can review and submit.
"""
import os
import re
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, Playwright

from . import config
from .config import ROOT

_GUARD_JS = """
(() => {
  const released = () => { try { return sessionStorage.getItem('appli_release') === '1'; } catch (e) { return false; } };
  window.addEventListener('submit', e => { if (!released()) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);
})();
"""

_SUBMITISH = re.compile(r"submit|createapplication|/applications?(\?|$)|/apply(/|\?|$)|/jobs/\d+(\?|$)", re.I)
_SAFE = re.compile(r"upload|presign|parse|autofill|track|analytics|telemetry|log|event|sentry|s3\.|amazonaws|recaptcha|hcaptcha|captcha", re.I)


def launch(pw: Playwright) -> BrowserContext:
    headless = os.getenv("APPLI_HEADLESS") == "1"
    # A visible window must size the page to itself: a fixed viewport larger than the screen crops the bottom of
    # the page (where "Submit application" lives) with no way to scroll to it.
    ctx = pw.chromium.launch_persistent_context(
        str((config.HOME or ROOT) / ".browser-profile"),  # each person's own cookies and site logins
        headless=headless,
        no_viewport=not headless,
        viewport={"width": 1280, "height": 900} if headless else None,
        args=["--disable-blink-features=AutomationControlled"] + ([] if headless else ["--start-maximized"]),
    )
    ctx.add_init_script(_GUARD_JS)
    return ctx


def install_guard(page: Page):
    def handler(route, request):
        url = request.url
        if request.method in ("POST", "PUT", "PATCH") and not _SAFE.search(url):
            same_path = urlparse(url).path == urlparse(page.url).path
            if same_path or _SUBMITISH.search(url):
                print(f"    [guard] blocked {request.method} {url[:110]}")
                return route.abort()
        return route.continue_()

    page.route("**/*", handler)


def release(page: Page):
    """Hand the page over to the human: lift both guards."""
    try:
        page.unroute_all(behavior="ignoreErrors")
    except Exception:
        pass
    try:
        page.evaluate("sessionStorage.setItem('appli_release','1')")
    except Exception:
        pass
