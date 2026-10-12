"""Ashby Yes / No button pairs are found on every extract, not just the first (the runner re-reads the form after the
resume upload and after each pass)."""
from playwright.sync_api import sync_playwright

from runner import forms

HTML = """<div class="ashby-application-form-field-entry">
<label class="ashby-application-form-question-title _required_x">Are you legally authorized to work in the United States?</label>
<div class="ashby-application-form-input-yesno"><button aria-pressed="false" data-option="yes">Yes</button>
<button aria-pressed="false" data-option="no">No</button><input type="checkbox" tabindex="-1"></div></div>"""

with sync_playwright() as pw:
    b = pw.chromium.launch()
    p = b.new_page()
    p.set_content(HTML)
    for i in (1, 2):
        assert [f.type for f in forms.extract(p)] == ["buttons"], f"extract #{i} lost the Yes / No question"
    assert len(forms.unfilled_required(p)) == 1, "an unanswered Yes / No question counts as left for you"
    b.close()
print("all yes/no checks passed")
