import asyncio
import json
import re
from datetime import date
from pathlib import Path

import pytest

from agentkit.llm import LLM
from agentkit.runner import Runner
from upworkbid.assess import Assessment, check_assessment
from upworkbid.jobs import apply_rules, client_score, parse_job, read_jobs
from upworkbid.output import write_outputs
from upworkbid.pipeline import CACHE_VERSION, assess_all
from upworkbid.profile import load_profile
from upworkbid.sample import make_sample, score

from .fakes import FakeClient, no_sleep, user_prompt

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "upworkbid"
TODAY = date(2026, 10, 12)


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    out = tmp_path_factory.mktemp("upworkbid")
    truth = make_sample(out)
    return out, truth, load_profile(out / "profile.toml")


def oracle(truth: dict, changes=None):
    def respond(kwargs):
        name = re.search(r"^JOB POST \((.+)\)$", user_prompt(kwargs), re.M).group(1)
        reply = dict(truth["assess"][name])
        reply.update((changes or {}).get(name, {}))
        return json.dumps(reply)
    return respond


def _run(case, tmp_path, responder=None):
    out, truth, p = case
    jobs, _ = read_jobs(out / "jobs")
    runner = Runner(LLM(FakeClient(responder or oracle(truth)), "test-model", rpm=0, sleep=no_sleep),
                    tmp_path / "cache", CACHE_VERSION)
    return asyncio.run(assess_all(jobs, p, runner, TODAY, progress=lambda _: None)), runner


# --- reading posts ------------------------------------------------------------------------------------------------

def test_posts_are_read_in_code(case):
    out, _, p = case
    jobs = {j.file: j for j in read_jobs(out / "jobs")[0]}
    j1 = jobs["job01_lead_agent.txt"]
    assert (j1.kind, j1.budget, j1.proposals, j1.connects) == ("fixed", 3000.0, "10 to 15", 16)
    assert (j1.payment_verified, j1.rating, j1.hire_rate, j1.total_spent) == (True, 4.9, 80, 45000.0)
    assert j1.member_since == date(2019, 3, 15) and j1.skills == ["python", "langchain", "hubspot", "openai api"]
    assert j1.questions == ["Which CRM have you connected an AI agent to before?"]
    assert "Activity on this job" not in j1.description and "200 inbound demo requests" in j1.description
    j4 = jobs["job04_policy_rag.txt"]
    assert (j4.kind, j4.rate_low, j4.rate_high, len(j4.questions)) == ("hourly", 80.0, 120.0, 2)
    assert jobs["job06_unverified.txt"].payment_verified is False
    assert client_score(jobs["job03_news_digest.txt"], TODAY)[0] == 40
    loose = parse_job("x.txt", "Quick fix\n\nBudget: $150\nhourly, about $20 /hr")
    assert loose.kind == "hourly" and loose.rate_high == 20.0 and loose.budget_text == "up to $20/h"


PAGE = """WhatsApp chatbot for our clinic bookings\r
\r
We want a WhatsApp and Telegram bot that books appointments. Free trial users should get reminders too.\r
\r
Hourly Range: $90.00-$130.00\r
\r
Skills and Expertise\r
\r
Mandatory skills\r
Python\r
OpenAI API\r
\r
Nice-to-have skills\r
Twilio\r
Activity on this job\r
Proposals: 5 to 10\r
Required Connects to submit a proposal: 14\r
About the client\r
Payment method verified\r
Rating is 4.7 out of 5.\r
4.7 of 12 reviews\r
"""


def test_a_page_copied_from_upwork_and_a_bot_job_that_is_not_a_scam(case):
    _, _, p = case
    j = parse_job("page.txt", PAGE)
    assert (j.kind, j.rate_low, j.rate_high, j.connects, j.rating) == ("hourly", 90.0, 130.0, 14, 4.7)
    assert j.skills == ["python", "openai api", "twilio"] and j.proposals == "5 to 10"
    apply_rules(j, p, TODAY)  # a WhatsApp bot is a job, not a request to talk on WhatsApp
    assert j.out == [] and j.notes == []
    for ask in ["Contact me on Telegram for details.", "WhatsApp: +1 555 0100", "telegram @fastjobs99",
                "We will pay you outside Upwork.", "Email us at 555-0100 after you apply."]:
        bad = parse_job("bad.txt", PAGE + ask)
        apply_rules(bad, p, TODAY)
        assert any("off Upwork" in x for x in bad.out), ask
    unpaid = parse_job("u.txt", PAGE + "First a free test task to see your skills.")
    apply_rules(unpaid, p, TODAY)
    assert unpaid.notes == ["asks for unpaid test work"]


def test_profile_settings(tmp_path):
    p = tmp_path / "p.toml"
    p.write_text('[profile]\nname = "A"\nmax_proposals = "lots"\n')
    with pytest.raises(ValueError, match="max_proposals"):
        load_profile(p)
    p.write_text('[profile]\nname = "A"\n[[portfolio]]\nid = "P1"\ntitle = "t"\nsummary = "s"\n'
                 '[[project_type]]\nname = "x"\nhours = [10, 5]\n')
    with pytest.raises(ValueError, match="hours = \\[low, high\\]"):
        load_profile(p)


# --- proposals ----------------------------------------------------------------------------------------------------

def test_proposal_checks_catch_invented_claims_contact_details_and_missing_answers(case):
    out, truth, p = case
    j = {x.file: x for x in read_jobs(out / "jobs")[0]}["job04_policy_rag.txt"]
    a = Assessment.model_validate({
        "project_type": "chatbot", "fit": 95, "opening_detail": "your amazing project",
        "proposal": "Dear Hiring Manager, I have 10 years of experience and 50 RAG projects (P9). Email me at "
                    "sam@mail.example or see https://other.example/work. Rate: [RATE].",
        "portfolio_used": ["P9"], "answers": ["Citations."]})
    d = check_assessment(a, j, p)
    holds = [c.text for c in d.checks if c.hold]
    assert not d.ready
    assert "project type 'chatbot' is not one of yours, so hours can't be estimated" in holds
    assert "proposal: banned phrase 'dear hiring manager'" in holds
    assert "proposal: contact details before a contract break Upwork's rules" in holds
    assert "proposal: link https://other.example/work is not one of your portfolio links" in holds
    assert "proposal: mentions 10, 50, 9, which isn't in your profile or the post" in holds
    assert "proposal: placeholder '[RATE]'" in holds
    assert "the opening isn't built on a detail from this job post" in holds
    assert "cites a portfolio item you don't have" in holds and "names no portfolio item" in holds
    assert "answers 1 of 2 screening questions" in holds


# --- the whole example --------------------------------------------------------------------------------------------

def test_committed_example_matches_the_generator(tmp_path):
    truth = make_sample(tmp_path)
    assert truth == json.loads((EXAMPLE / "truth.json").read_text())
    for f in tmp_path.rglob("*"):
        if f.is_file():
            rel = f.relative_to(tmp_path)
            assert f.read_bytes() == (EXAMPLE / rel).read_bytes(), rel


def test_end_to_end_scores_full_marks(case, tmp_path):
    out, truth, p = case
    decisions, runner = _run(case, tmp_path)
    assert runner.llm.usage.requests == 4  # the rules skip the other 4 posts for free
    stats = write_outputs(tmp_path, decisions)
    s = score(json.loads((tmp_path / "jobs.json").read_text()), truth)
    assert (s["decision_right"], s["jobs"], s["wrong_decisions"]) == (8, 8, [])
    assert (s["reason_right"], s["reason_expected"]) == (4, 4)
    assert (s["rate_right"], s["rate_expected"], s["ready"], s["bids"]) == (4, 4, 3, 3)
    assert [d.job.file for d in decisions[:3]] == ["job04_policy_rag.txt", "job01_lead_agent.txt",
                                                   "job08_invoice_pdf.txt"]
    job08 = next(d for d in decisions if d.job.file == "job08_invoice_pdf.txt")
    assert job08.decision == "bid" and job08.notes == ["$72/h after fees if it takes 25 h: scope it tightly"]
    assert stats == {"bids": 3, "maybe": 1, "skips": 4, "connects": 42, "connects_cost": 6.3}
    text = (tmp_path / "proposals" / "job04_policy_rag.txt").read_text()
    assert text.startswith("Job: RAG assistant over our HR policy PDFs\nFile: job04_policy_rag.txt\n"
                           "Budget: $80-$120/h (about $108/h after fees)\nDecision: bid")
    assert "2. Which vector database would you use and why?\nFor a few hundred PDFs" in text
    assert text.rstrip().endswith("Sam") is False and "\n\nSam\n" in text
    assert sorted(x.name for x in (tmp_path / "proposals").iterdir()) == [
        "job01_lead_agent.txt", "job03_news_digest.txt", "job04_policy_rag.txt", "job08_invoice_pdf.txt"]


def test_a_low_rate_or_weak_fit_is_skipped_after_the_model(case, tmp_path):
    out, truth, p = case
    changed = {"job08_invoice_pdf.txt": {"project_type": "RAG chatbot"},  # a bigger job for the same money
               "job01_lead_agent.txt": {"fit": 35}}
    decisions, _ = _run(case, tmp_path, oracle(truth, changed))
    by = {d.job.file: d for d in decisions}
    assert by["job08_invoice_pdf.txt"].decision == "skip"
    assert by["job08_invoice_pdf.txt"].reasons == ["about $65/h after fees at your 20-35 h estimate"]
    assert by["job01_lead_agent.txt"].decision == "skip" and by["job01_lead_agent.txt"].reasons == ["fit 35"]


# --- command line -------------------------------------------------------------------------------------------------

def test_cli_jobs_run_and_score(tmp_path, monkeypatch, capsys):
    from upworkbid import __main__ as cli

    ex = tmp_path / "ex"
    cli.main(["sample", "--out", str(ex)])
    truth = json.loads((ex / "truth.json").read_text())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(cli.LLM, "from_settings",
                        classmethod(lambda cls, s: LLM(FakeClient(oracle(truth)), s.model, rpm=0, sleep=no_sleep)))
    base = ["--profile", str(ex / "profile.toml"), "--jobs", str(ex / "jobs"), "--today", "2026-10-12"]
    cli.main(["jobs", *base])
    assert "8 posts; 4 left to assess = 4 model requests." in capsys.readouterr().out
    out = tmp_path / "out"
    cli.main(["run", *base, "--out", str(out)])
    printed = capsys.readouterr().out
    assert "Done: 3 to bid on, 1 maybe, 4 skipped; 4 model requests" in printed
    assert "Bidding on all 3 costs 42 Connects (about $6.30)." in printed
    cli.main(["score", "--run", str(out), "--truth", str(ex / "truth.json")])
    assert "Jobs decided right (bid / maybe / skip): 8 of 8 (100%)" in capsys.readouterr().out
