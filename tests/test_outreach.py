import asyncio
import csv
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import openai
import pytest

from agentkit.inbox import Email, read_inbox, split_thread
from agentkit.llm import LLM
from agentkit.runner import Runner
from outreach.billing import bill, read_meetings
from outreach.campaign import load_campaign
from outreach.leads import bar_problems, employee_range, read_leads, screen
from outreach.output import write_reply_files, write_sequence_files
from outreach.pipeline import CACHE_VERSION, triage_replies, write_sequences
from outreach.replies import Triage, check_triage, code_category, later_dates
from outreach.sample import make_sample, score
from outreach.sequence import SequenceDraft, check_sequence

from .fakes import FakeClient, no_sleep, user_prompt

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "outreach"
CENTRAL = timezone(timedelta(hours=-5))


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    out = tmp_path_factory.mktemp("outreach")
    truth = make_sample(out)
    c = load_campaign(out / "campaign.toml")
    leads = read_leads(out / "leads.csv")
    screen(leads, c)
    return out, truth, c, leads


def _lead(leads, first):
    return next(x for x in leads if x.first_name == first)


# --- a perfect stand-in model -------------------------------------------------------------------------------------

def _facts(prompt: str) -> dict:
    block = prompt.split("LEAD FACTS\n", 1)[1].split("\n\n", 1)[0]
    return dict(line[2:].split(": ", 1) for line in block.splitlines() if line.startswith("- "))


def good_sequence(prompt: str) -> dict:
    f = _facts(prompt)
    first, company = f["Name"].split()[0], f["Company"]
    fact = f.get("Recent signal") or f.get("Industry") or company
    return {"subject": f"{company} IT help", "personal_fact": fact, "emails": [
        {"step": 1, "proof_used": ["P1"], "body": (
            f"Hi {first},\n\nI saw this about {company}: {fact}. Growth like that usually lands on whoever fixes the "
            "laptops. Brightline IT runs the help desk, Microsoft 365, backups and security training for firms with "
            "20-200 staff, for a flat monthly fee per user. For a 60-person accounting firm we cut help-desk tickets "
            "by 38% in six months.\n\nWorth a 20-minute call next week?")},
        {"step": 2, "proof_used": ["P2"], "body": (
            f"Hi {first}, one more reason to talk: our average first response time is 12 minutes during business "
            "hours. If it's easier, pick a time here: https://cal.example/brightline/intro")},
        {"step": 3, "proof_used": ["P3"], "body": (
            f"Hi {first}, last note from me. We helped 14 clients pass cyber-insurance security questionnaires in "
            f"2025. If that would help {company}, I'm glad to talk.")}]}


DRAFTS = {
    "interested": "Great - Thursday afternoon works. I'll send an invite for 2pm.",
    "question": "Good questions. Pricing depends on headcount, so I'll get you a number for Ridgeline this week, and "
                "I'll confirm how we handle Revit licensing.",
    "not_now": "Understood, and good luck with the merger. I'll reach out in January.",
    "referral": "Thanks, Ben - I'll reach out to Hannah and mention you sent me.",
    "objection": "Fair enough - glad it's working. If your cyber-insurance renewal brings a security questionnaire, "
                 "we've helped 14 clients pass theirs, and I'm happy to share how.",
}


def oracle(truth: dict):
    by_file = {r["file"]: r for r in truth["replies"]}

    def respond(kwargs):
        prompt = user_prompt(kwargs)
        if prompt.startswith("LEAD FACTS"):
            return json.dumps(good_sequence(prompt))
        t = by_file[re.search(r"^Reply file: (.+)$", prompt, re.M).group(1)]
        newest = prompt.split("### Newest message\n", 1)[1].split("\n\n###", 1)[0]
        quote = re.split(r"(?<=[.?!])\s", newest.split("\n\n")[1] if newest.startswith("Hi ") else newest)[0]
        return json.dumps({"category": t["category"], "quote": quote, "summary": t["category"],
                           "follow_up_date": t["follow_up"], "referral_name": t["referral_name"],
                           "referral_email": t["referral_email"], "questions": [],
                           "needs_client": t["needs_client"],
                           "needs_client_reason": "price per user; Revit licensing" if t["needs_client"] else "",
                           "reply": DRAFTS.get(t["category"], ""), "proof_used": []})
    return respond


def _runner(tmp_path, responder, version=CACHE_VERSION):
    return Runner(LLM(FakeClient(responder), "test-model", rpm=0, sleep=no_sleep), tmp_path / "cache", version)


# --- campaign and leads -------------------------------------------------------------------------------------------

def test_campaign_settings_are_checked(case, tmp_path):
    _, _, c, _ = case
    assert c.client == "Brightline IT" and c.steps == 3 and c.countries == ["US"]
    assert c.sending_domains == ["brightline-it.example", "trybrightline.example"]
    assert "400 Market St" in c.footer_text() and "no thanks" in c.footer_text()
    bad = tmp_path / "bad.toml"
    bad.write_text('[client]\nname = "X"\nsender_name = "Y"\noffer = "Z"\n')
    with pytest.raises(ValueError, match="postal_address"):
        load_campaign(bad)
    bad.write_text('[client]\nname = "X"\nsender_name = "Y"\noffer = "Z"\npostal_address = "A"\ncolour = "red"\n')
    with pytest.raises(ValueError, match="unknown \\[client\\] settings: colour"):
        load_campaign(bad)


def test_leads_are_screened_in_code(case, tmp_path):
    out, _, c, leads = case
    held = {x.first_name: x.held for x in leads if x.held}
    assert set(held) == {"Tom", "Grace", "Jordan"}
    assert "12 employees; the bar is 20-200" in held["Tom"][0] and "CA is outside US" in held["Grace"][0]
    assert _lead(leads, "Priya").notes == ["no recent signal, so the first line uses the company or industry"]
    again = read_leads(out / "leads.csv")
    screen(again, c, {"maya.chen@harlowpike.example", "@tranpt.example"})
    assert _lead(again, "Maya").held == ["on the do-not-contact list"] and _lead(again, "Kevin").held
    assert employee_range("51-200") == (51, 200) and employee_range("1,000+") == (1000, 0)
    assert bar_problems("CEO", "11-50", "US", c) == ([], ["company size 11-50 straddles the bar"])
    assert bar_problems("Intern", "60", "", c) == (["title 'Intern' is not one the client wants"], ["country unknown"])


# --- sequences ----------------------------------------------------------------------------------------------------

def test_a_good_sequence_is_ready_and_gets_the_legal_footer(case):
    _, _, c, leads = case
    maya = _lead(leads, "Maya")
    from outreach.sequence import build_prompt
    seq = check_sequence(SequenceDraft.model_validate(good_sequence(build_prompt(maya, c))), maya, c)
    assert seq.flags == [] and not seq.held
    assert len(seq.bodies) == 3 and all(b.endswith("Reply \"no thanks\" and I won't email again.") for b in seq.bodies)
    assert "400 Market St, Suite 210, Springfield, IL 62701" in seq.bodies[0]


def test_made_up_claims_links_and_fake_threads_are_held(case):
    _, _, c, leads = case
    maya = _lead(leads, "Maya")
    draft = SequenceDraft.model_validate({
        "subject": "Re: our call", "personal_fact": "Maya just raised a Series A",
        "emails": [{"step": 1, "body": "Hi Maya, I hope this email finds you well. We cut tickets 45% for 100+ firms. "
                                       "See https://brightline-it.example/case-study"},
                   {"step": 2, "body": "Hi {first_name}, see www.competitor.example/pricing"}]})
    seq = check_sequence(draft, maya, c)
    texts = [f.text for f in seq.flags if f.hold]
    assert seq.held
    assert "subject starts with 'Re:' or 'Fwd:' on a first email (deceptive)" in texts
    assert any(t.startswith("the opening fact is not in the lead's data") for t in texts)
    assert "banned phrase: 'hope this email finds you well'" in texts
    assert "mentions 45, 100, which is not in the offer, proof or lead data" in texts
    assert "a link in the first email (hurts deliverability); remove it" in texts
    assert "unfilled placeholder '{first_name}'" in texts
    assert any("www.competitor.example/pricing is not the client's booking link" in t for t in texts)
    assert "email 3 is missing" in texts


# --- replies ------------------------------------------------------------------------------------------------------

def _email(body: str, sender="sofia@marininsurance.example", subject="Re: hello", sent=datetime(2026, 10, 7, 9, 0)):
    latest, earlier = split_thread(body)
    return Email(file="x.txt", sender=sender, sender_name="", subject=subject, sent=sent.replace(tzinfo=CENTRAL),
                 latest=latest, earlier=earlier)


def test_rules_decide_bounces_opt_outs_and_away_messages(case):
    out, _, _, _ = case
    emails, _ = read_inbox(out / "replies")
    assert [code_category(e) for e in emails] == ["", "", "", "unsubscribe", "bounce", "out_of_office", "", ""]
    assert code_category(_email("Not interested. Please stop emailing me.")) == "unsubscribe"
    assert code_category(_email("Unsubscribe")) == "unsubscribe"
    assert code_category(_email("Don't email me again")) == "unsubscribe"
    # a newsletter footer or "no newsletters" is not an opt-out
    assert code_category(_email("Sure, send times. To unsubscribe from our newsletter, visit x.example.")) == ""
    assert code_category(_email("Sure - but please don't add me to any newsletters.")) == ""


def test_later_dates_are_worked_out_from_the_day_sent():
    wed = date(2026, 10, 7)
    assert later_dates("Reach out again in January", wed) == {date(2027, 1, 1)}
    assert later_dates("try me next quarter", wed) == {date(2027, 1, 1)}
    assert later_dates("after Q2 2027", wed) == {date(2027, 4, 1)}
    assert later_dates("ping me in 3 weeks", wed) == {date(2026, 10, 28)}
    assert later_dates("in a couple of months", wed) == {date(2026, 12, 7)}
    assert later_dates("in November", wed) == {date(2026, 11, 2)}  # Nov 1, 2026 is a Sunday
    assert later_dates("out until Monday, October 19", wed) == {date(2026, 10, 12), date(2026, 10, 19)}


def test_triage_checks_referrals_drafts_and_numbers(case):
    _, _, c, leads = case
    ben = _lead(leads, "Ben")
    e = _email("I don't handle IT. Hannah Brooks does: hannah.brooks@westfielddental.example", sender=ben.email)
    r = check_triage(Triage(category="referral", referral_name="Hannah Brooks",
                            referral_email="hannah@westfielddental.example", reply="Thanks Ben!"), e, ben, c)
    assert r.referral_email == "hannah.brooks@westfielddental.example"  # the one address actually in the reply
    assert any(f.text == "referral email hannah@westfielddental.example is not in the reply" for f in r.flags)

    sofia = _lead(leads, "Sofia")
    r2 = check_triage(Triage(category="not_interested", reply="Totally understand!"), _email("No thanks, we're set."),
                      sofia, c)
    assert r2.reply == "" and r2.suppress_email == "sofia@marininsurance.example" and r2.suppress_reason == "declined"

    r3 = check_triage(Triage(category="interested", reply="Great - we cut costs 50% for clients like you."),
                      _email("Sure, let's talk."), sofia, c)
    assert "https://cal.example/brightline/intro" in r3.reply and r3.reply.endswith("Partner, Brightline IT")
    assert any("mentions 50" in f.text and f.hold for f in r3.flags)

    r4 = check_triage(Triage(category="question", needs_client=True, needs_client_reason="price per user",
                             reply="I'll find out."), _email("What does it cost?"), sofia, c)
    assert any(f.hold and f.text == "needs the client: price per user" for f in r4.flags)


# --- the whole example --------------------------------------------------------------------------------------------

def test_committed_example_matches_the_generator(tmp_path):
    truth = make_sample(tmp_path)
    assert truth == json.loads((EXAMPLE / "truth.json").read_text())
    for f in [*(tmp_path / "replies").iterdir(), *(p for p in tmp_path.iterdir() if p.is_file())]:
        rel = f.relative_to(tmp_path)
        assert f.read_bytes() == (EXAMPLE / rel).read_bytes(), rel


def test_end_to_end_on_the_synthetic_campaign_scores_full_marks(case, tmp_path):
    out, truth, c, leads = case
    runner = _runner(tmp_path, oracle(truth))
    seqs = asyncio.run(write_sequences(leads, c, runner, progress=lambda _: None))
    emails, _ = read_inbox(out / "replies")
    results = asyncio.run(triage_replies(emails, leads, c, runner, progress=lambda _: None))
    assert runner.llm.usage.requests == 8 + 5  # bounces, opt-outs and away messages need no model
    write_sequence_files(tmp_path, seqs, c)
    write_reply_files(tmp_path, results, c)
    s = score(json.loads((tmp_path / "sequences.json").read_text()), json.loads((tmp_path / "replies.json").read_text()),
              truth)
    assert (s["written_right"], s["leads"], s["ready"], s["should_write"]) == (11, 11, 8, 8)
    assert (s["personal_right"], s["with_signal"]) == (7, 7)
    assert (s["category_right"], s["replies"], s["wrong_category"]) == (8, 8, [])
    assert (s["suppress_right"], s["suppress_expected"], s["wrong_suppressions"]) == (2, 2, [])
    assert (s["follow_up_right"], s["follow_up_expected"]) == (2, 2)
    assert (s["referral_right"], s["draft_right"], s["needs_client_right"]) == (1, 8, 1)

    with open(tmp_path / "sequences.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 8 and rows[0]["subject"] == "Harlow & Pike CPAs IT help"
    assert rows[0]["email_1"].startswith("Hi Maya,") and "https://cal.example" not in rows[0]["email_1"]
    with open(tmp_path / "suppress.csv") as f:
        assert {r["email"]: r["reason"] for r in csv.DictReader(f)} == {
            "priya@castlerockdental.example": "asked not to be contacted", "kevin.tran@tranpt.example": "bounced"}
    with open(tmp_path / "followups.csv") as f:
        follow = list(csv.DictReader(f))
    assert [(r["follow_up"], r["reason"]) for r in follow] == [("2026-10-19", "out of office"), ("2027-01-01", "not now")]
    assert follow[0]["other_contacts"] == "jen.walsh@lakeside-logistics.example"
    with open(tmp_path / "new_leads.csv") as f:
        new = list(csv.DictReader(f))
    assert [(r["first_name"], r["email"], r["company"]) for r in new] == [
        ("Hannah", "hannah.brooks@westfielddental.example", "Westfield Dental Group")]
    with open(tmp_path / "triage.csv") as f:
        triage = list(csv.DictReader(f))
    assert triage[0]["category"] == "interested" and triage[-1]["category"] == "bounce"
    drafts = sorted(p.name for p in (tmp_path / "drafts").iterdir())
    assert drafts == ["01_maya_interested.txt", "02_derek_question.txt", "03_alicia_later.txt",
                      "07_ben_referral.txt", "08_sofia_happy.txt"]
    maya = (tmp_path / "drafts" / "01_maya_interested.txt").read_text()
    assert maya.startswith("To: maya.chen@harlowpike.example\nSubject: Re: help desk at Harlow & Pike")
    assert "https://cal.example/brightline/intro" in maya

    # everything comes from the cache the second time
    again = _runner(tmp_path, lambda kw: (_ for _ in ()).throw(AssertionError("should come from cache")))
    asyncio.run(write_sequences(leads, c, again, progress=lambda _: None))
    asyncio.run(triage_replies(emails, leads, c, again, progress=lambda _: None))
    assert again.from_cache == 13 and again.llm.usage.requests == 0


def test_opt_outs_are_honored_even_when_the_model_is_capped(case, tmp_path):
    out, truth, c, leads = case

    def capped(kwargs):
        req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        raise openai.RateLimitError("Rate limit exceeded: free-models-per-day", response=httpx.Response(429, request=req),
                                    body=None)

    runner = _runner(tmp_path, capped)
    emails, _ = read_inbox(out / "replies")
    results = asyncio.run(triage_replies(emails, leads, c, runner, progress=lambda _: None))
    assert "daily request limit" in runner.stopped_reason
    by = {r.email.file: r for r in results}
    assert by["04_priya_remove.txt"].suppress_email == "priya@castlerockdental.example"
    assert by["05_bounce_kevin.eml"].suppress_email == "kevin.tran@tranpt.example"
    assert by["06_marcus_away.eml"].follow_up == "2026-10-19"
    assert by["01_maya_interested.eml"].error.startswith("not processed yet")


def test_the_model_over_reaching_is_caught_end_to_end(case, tmp_path):
    out, truth, c, leads = case
    base = oracle(truth)

    def sloppy(kwargs):
        reply = json.loads(base(kwargs))
        prompt = user_prompt(kwargs)
        if "Name: Derek Osei" in prompt:
            reply["emails"][0]["body"] = reply["emails"][0]["body"].replace("38%", "52%")  # an invented result
        if "Reply file: 07_ben_referral.eml" in prompt:
            reply["referral_email"] = "hannah@westfield.example"  # a guessed address
        if "Reply file: 03_alicia_later.eml" in prompt:
            reply["follow_up_date"] = "2026-12-01"  # not what she said
        return json.dumps(reply)

    runner = _runner(tmp_path, sloppy)
    seqs = asyncio.run(write_sequences(leads, c, runner, progress=lambda _: None))
    derek = next(s for s in seqs if s.lead.first_name == "Derek")
    assert derek.held and any("mentions 52" in f.text for f in derek.flags)
    emails, _ = read_inbox(out / "replies")
    results = {r.email.file: r for r in asyncio.run(triage_replies(emails, leads, c, runner, progress=lambda _: None))}
    assert results["07_ben_referral.eml"].referral_email == "hannah.brooks@westfielddental.example"
    assert results["03_alicia_later.eml"].follow_up == "2027-01-01"
    assert any("the model said 2026-12-01" in f.text for f in results["03_alicia_later.eml"].flags)


def test_only_held_meetings_inside_the_bar_are_billed(case):
    out, truth, c, leads = case
    rows, totals = bill(read_meetings(out / "meetings.csv"), leads, c)
    assert [r["billable"] for r in rows] == ["yes", "no", "yes", "no"]
    assert [r["email"] for r in rows if r["billable"] == "yes"] == truth["invoice"]["billable"]
    assert totals["invoice_total"] == truth["invoice"]["total"] == 1000.0
    assert "company facts from ben.carter@westfielddental.example" in rows[2]["reason"]


# --- command line -------------------------------------------------------------------------------------------------

def test_cli_sample_write_replies_bill_and_score(tmp_path, monkeypatch, capsys):
    from outreach import __main__ as cli

    ex = tmp_path / "ex"
    cli.main(["sample", "--out", str(ex)])
    truth = json.loads((ex / "truth.json").read_text())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(cli.LLM, "from_settings",
                        classmethod(lambda cls, s: LLM(FakeClient(oracle(truth)), s.model, rpm=0, sleep=no_sleep)))
    out = tmp_path / "out"
    common = ["--campaign", str(ex / "campaign.toml"), "--leads", str(ex / "leads.csv")]
    cli.main(["write", *common, "--out", str(out), "--model", "x:free"])
    printed = capsys.readouterr().out
    assert "WARNING: free models may log prompts" in printed
    assert "Done: 8 of 8 sequences ready to send, 0 to fix, 3 leads held back" in printed
    cli.main(["replies", *common, "--replies", str(ex / "replies"), "--out", str(out)])
    printed = capsys.readouterr().out
    assert "5 drafts; 5 model requests" in printed and "(2 addresses never to email again)" in printed
    cli.main(["bill", *common, "--meetings", str(ex / "meetings.csv"), "--out", str(out)])
    assert "= $1,000" in capsys.readouterr().out
    for name in ("sequences.csv", "sequences.xlsx", "review_sequences.csv", "triage.csv", "suppress.csv",
                 "followups.csv", "new_leads.csv", "invoice.csv", "sequences_summary.json", "replies_summary.json"):
        assert (out / name).exists(), name
    cli.main(["score", "--run", str(out), "--truth", str(ex / "truth.json")])
    report = capsys.readouterr().out
    assert "Replies sorted right: 8 of 8 (100%)" in report and "Opt-outs and bounces suppressed: 2 of 2 (100%)" in report
    assert "Sequences ready to send (nothing to fix): 8 of 8 (100%)" in report
