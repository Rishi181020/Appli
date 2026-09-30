"""Offline checks for the Jev tier, known answers, learning rules and the resume guardrail (Jev HTTP is stubbed).
Run: python -m tests.test_jev"""
from runner import answering, config, jev, known
from runner.answering import Field
from runner.profile import load_profile
from runner.resume import select

from runner.db import user_id

config.activate(user_id())  # the files of whoever last signed in on this computer
profile = load_profile(config.PROFILE_PATH, config.PRIMARY_TEX)
job = {"id": 1, "role": "Software Engineer", "company": "Acme Robotics"}
sent = []


def stub(answers_by_label):
    """Fake jev.decide: answers each question by looking up a phrase from its instructions."""
    def decide(state, questions, session_id=None, timeout=60, **kw):
        sent.append((state, questions))
        out = {}
        for qid, q in questions.items():
            for phrase, (key, conf) in answers_by_label.items():
                if phrase in q["instructions"]:
                    out[qid] = {"type": "choice", "choice": key, "confidence": conf}
        return out
    return decide


known_answers = [
    {"question_text": "Are you willing to relocate?", "answer": "Yes"},
    {"question_text": "How many years of professional software experience do you have?", "answer": "2"},
    {"question_text": "Do you have experience with Python?", "answer": "Yes"},
]

# --- option fields map onto the form's own options; low confidence and "unknown" are flagged, never guessed ---
jev.decide = stub({
    "Do you have hands-on Python experience": ("o0", 0.95),
    "Are you open to relocating": ("o0", 0.41),        # below the 0.6 bar
    "Do you hold a pilot license": ("unknown", 0.9),  # Jev says the profile doesn't settle it
    "Years of software experience": ("k0", 0.9),      # text field -> matched to a known question
    "Favourite colour": ("none", 0.95),              # text field, no known match -> chat model
})
fields = [
    Field("a", "Do you have hands-on Python experience?", "radio", ["Yes", "No"]),
    Field("b", "Are you open to relocating?", "select", ["Yes", "No"], required=True),
    Field("c", "Do you hold a pilot license?", "radio", ["Yes", "No"]),
    Field("d", "Years of software experience", "text"),
]
chat_calls = []
answering.llm.chat_json = lambda *a, **k: (chat_calls.append(a), {"answers": {}})[1]
r = answering.answer_fields(fields, profile, {}, job, known=known_answers)
assert r.values.get("a") == "Yes" and r.tiers["a"] == "jev" and r.confidence["a"] == 0.95, r
assert "b" not in r.values and any(u["id"] == "b" and "0.41" in u["reason"] for u in r.unanswered), r.unanswered
assert "c" not in r.values and any(u["id"] == "c" for u in r.unanswered)
# the text field went to Jev as "which known question is the same" and got that saved answer verbatim
assert r.values.get("d") == "2" and r.tiers["d"] == "jev", r.values
state, questions = sent[-1]
assert "pilot" in questions["f_c"]["instructions"] and "unknown" in questions["f_c"]["criteria"]
assert questions["f_a"]["criteria"]["o0"] == "Yes" and questions["f_a"]["criteria"]["o1"] == "No"
assert not chat_calls, "nothing should have gone to the chat model"
assert len(sent) == 1, "one Jev request per form"

# --- legal/consent questions never reach Jev ---
sent.clear()
r2 = answering.answer_fields([Field("x", "Have you ever been convicted of a felony?", "radio", ["Yes", "No"], True)],
                             profile, {}, job, known=known_answers)
assert not r2.values and not sent

# --- Jev outage falls back to the chat model for that form ---
def boom(*a, **k):
    raise jev.JevError("down")
jev.decide = boom
answering.llm.chat_json = lambda *a, **k: {"answers": {"a": "Yes"}}
r3 = answering.answer_fields([Field("a", "Do you have hands-on Python experience?", "radio", ["Yes", "No"])],
                             profile, {}, job, known=known_answers)
assert r3.values == {"a": "Yes"} and r3.tiers["a"] == "choice" and r3.notes, r3

# --- similarity prefilter keeps the request small and relevant ---
many = [{"question_text": f"Unrelated question number {i} about gardening", "answer": "x"} for i in range(200)] + known_answers
top = answering.similar_known("Are you willing to relocate for this job?", many)
assert top and top[0]["question_text"] == "Are you willing to relocate?" and len(top) <= 8

# --- learning rules ---
f_ok = Field("1", "Do you have experience with Python?", "radio", ["Yes", "No"])
assert known.learnable(f_ok, "jev", "Acme Robotics")
assert not known.learnable(f_ok, "open", "Acme Robotics"), "essays are never learned"
assert not known.learnable(Field("2", "Current company", "text"), "choice", None), "free text is never learned"
assert not known.learnable(Field("3", "Are you willing to work onsite in our Austin office?", "radio", ["Yes", "No"]), "jev", None)
assert not known.learnable(Field("4", "Why Acme Robotics? Have you used Acme products?", "radio", ["Yes", "No"]), "jev", "Acme Robotics")
assert not known.learnable(Field("5", "Have you ever been convicted of a crime?", "radio", ["Yes", "No"]), "jev", None)
assert not known.learnable(Field("6", "How did you hear about us?", "select", ["LinkedIn", "Other"]), "jev", None)

# --- resume guardrail: Jev may break near-ties but can't pick a much weaker resume ---
terms = [{"term": "Python", "required": True}]
scores = {"swe": {"pct": 0.70, "matched": [], "missing": []}, "ai": {"pct": 0.64, "matched": [], "missing": []},
          "fde": {"pct": 0.40, "matched": [], "missing": []}}
select.jev_pick = lambda job, t, s: ("ai", 0.9)
assert select.choose(job, terms, scores) == ("ai", "jev", 0.9), "within 10 points: Jev's pick stands"
select.jev_pick = lambda job, t, s: ("fde", 0.9)
assert select.choose(job, terms, scores)[:2] == ("swe", "score"), "30 points worse: overridden"
select.jev_pick = lambda job, t, s: (None, 0.3)
assert select.choose(job, terms, scores)[:2] == ("swe", "score"), "low confidence: best coverage"
print("all jev checks passed")
