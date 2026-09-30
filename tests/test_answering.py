"""Offline checks for tier routing (LLM calls are stubbed). Run: python -m tests.test_answering"""
from runner import answering, config, llm
from runner.answering import Field
from runner.profile import load_profile

from runner.db import user_id

config.activate(user_id())  # the files of whoever last signed in on this computer
profile = load_profile(config.PROFILE_PATH, config.PRIMARY_TEX)
calls = {"json": [], "text": []}


def fake_json(system, user, model=None, max_tokens=3000, validate=None, **kw):
    calls["json"].append(user)
    obj = {"answers": {"q_pref": "Remote", "q_referral": None, "q_ml": "Yes"}}
    validate(obj)
    return obj


def fake_text(system, user, model=None, max_tokens=1500, **kw):
    calls["text"].append(user)
    return "stub essay"


llm.chat_json, llm.chat_text = fake_json, fake_text

fields = [
    Field("first", "First Name *", "text"),
    Field("email", "Email", "email"),
    Field("auth", "Are you legally authorized to work in the United States?", "radio", ["Yes", "No"]),
    Field("spons", "Will you now or in the future require sponsorship?", "radio", ["Yes", "No"]),
    Field("race", "Race / Ethnicity", "select", ["Asian (Not Hispanic or Latino)", "White", "Decline to self-identify"]),
    Field("vet", "Veteran status", "select", ["I am a protected veteran", "I am not a protected veteran"]),
    Field("q_pref", "Work arrangement preference", "select", ["Onsite", "Hybrid", "Remote"]),
    Field("q_referral", "Who referred you?", "text"),
    Field("q_ml", "Do you have experience with machine learning?", "radio", ["Yes", "No"]),
    Field("why", "Why do you want to work at Acme?", "textarea", required=True),
    Field("extra", "Anything else you would like to add?", "textarea"),
    Field("resume", "Resume", "file"),
]
job = {"role": "Software Engineer", "company": "Acme"}
bank = {}
res = answering.answer_fields(fields, profile, bank, job, "resume text", use_jev=False)

assert res.values["first"] == "Rishi" and res.tiers["first"] == "direct"
assert res.values["email"] == profile.email
assert res.values["auth"] == "Yes" and res.tiers["auth"] == "direct"
assert res.values["spons"] == "Yes" and res.tiers["spons"] == "direct"  # candidate needs sponsorship
assert res.values["race"] == "Asian (Not Hispanic or Latino)" and res.tiers["race"] == "direct"
assert "vet" in res.tiers or any(u["id"] == "vet" for u in res.unanswered)  # 'No' can't map to that option -> model tier
assert res.values["q_pref"] == "Remote" and res.tiers["q_pref"] == "choice"
assert any(u["id"] == "q_referral" for u in res.unanswered), "unknown fact must be flagged, not invented"
assert res.tiers["why"] == "open" and res.values["why"] == "stub essay"
assert "resume" not in res.values
assert "extra" not in res.values and any(u["id"] == "extra" for u in res.unanswered), "optional essays are skipped"
assert len(calls["json"]) == 1, "all choice fields must share one cheap-model call"
assert len(calls["text"]) == 1

# learned answers bank wins and costs nothing
bank = {answering.norm("Who referred you?"): "Nobody"}
res2 = answering.answer_fields([Field("q_referral", "Who referred you?", "text")], profile, bank, job, use_jev=False)
assert res2.values["q_referral"] == "Nobody" and res2.tiers["q_referral"] == "direct"
print("all answering checks passed;", "direct:", sum(t == "direct" for t in res.tiers.values()),
      "choice:", sum(t == "choice" for t in res.tiers.values()), "open:", sum(t == "open" for t in res.tiers.values()))

# keyword rules in the answers bank
bank = {"~how did you hear|referred by": "LinkedIn", "~compensation|salary": "Open to discuss"}
r3 = answering.answer_fields(
    [Field("a", "How did you hear about us?", "text"), Field("b", "What is your compensation expectation?", "text")],
    profile, bank, job, use_jev=False)
assert r3.values == {"a": "LinkedIn", "b": "Open to discuss"} and set(r3.tiers.values()) == {"direct"}
print("keyword rule checks passed")

# conditional follow-ups are left blank instead of invented; fuzzy option matching
r4 = answering.answer_fields(
    [Field("a", '(a) If the answer is "Yes," please provide details below:', "textarea", required=True),
     Field("b", "If you answered yes to the question above, please provide the names", "text", required=True)],
    profile, {}, job, "resume", use_jev=False)
assert not r4.values and len(r4.unanswered) == 2, r4
assert answering.fuzzy_option("Computer Science and Engineering", ["Computer Engineering", "Computer Science", "Physics"]) == "Computer Science"
assert answering.fuzzy_option("Master's Degree", ["Bachelor's Degree", "Master's Degree (MS)"]) == "Master's Degree (MS)"
assert answering.fuzzy_option("Zebra", ["Computer Science", "Physics"]) is None
print("follow-up + fuzzy checks passed")

# legal / consent questions never reach the model and stay blank unless saved
calls_before = len(calls["json"])
r5 = answering.answer_fields(
    [Field("a", "Are you related to or in a close personal relationship with anyone currently at Acme?", "select", ["Yes", "No"], True),
     Field("b", "By selecting YES, I consent to receive recruiting SMS messages", "select", ["Yes", "No"], True),
     Field("c", "Have you ever been convicted of a felony?", "radio", ["Yes", "No"], True)],
    profile, {}, job, use_jev=False)
assert not r5.values and len(r5.unanswered) == 3 and len(calls["json"]) == calls_before, r5
saved = {answering.norm("Are you related to or in a close personal relationship with anyone currently at Acme?"): "No"}
r6 = answering.answer_fields(
    [Field("a", "Are you related to or in a close personal relationship with anyone currently at Acme?", "select", ["Yes", "No"], True)],
    profile, saved, job, use_jev=False)
assert r6.values == {"a": "No"} and r6.tiers["a"] == "direct"
print("legal/consent guard checks passed")

# education: the n-th school block gets the n-th profile entry; month/year/degree map onto option lists
sch = answering.answer_fields(
    [Field("s1", "School", "combobox", occ=1), Field("d1", "Degree", "combobox", ["Associate's Degree", "Bachelor's Degree", "Master's Degree"], occ=1),
     Field("m1", "Start date month", "combobox", ["January", "September", "June"], occ=1), Field("y1", "Start date year", "number", occ=1),
     Field("s2", "School", "combobox", occ=2), Field("d2", "Degree", "combobox", ["Associate's Degree", "Bachelor's Degree", "Master's Degree"], occ=2),
     Field("f2", "Discipline", "combobox", occ=2), Field("e2", "End date year", "number", occ=2),
     Field("s3", "School", "combobox", occ=3)],
    profile, {}, job, use_jev=False)
assert sch.values["s1"] == "Santa Clara University" and sch.values["d1"] == "Master's Degree", sch.values
assert sch.values["m1"] == "September" and sch.values["y1"] == "2025"
assert sch.values["s2"] == "Vishwakarma Institute of Information Technology" and sch.values["d2"] == "Bachelor's Degree"
assert sch.values["f2"] == "Information Technology" and sch.values["e2"] == "2023"
assert "s3" not in sch.values, "a third block has no profile entry and stays empty"
print("education checks passed")
