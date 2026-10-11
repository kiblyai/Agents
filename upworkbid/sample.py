"""A synthetic freelancer profile and 8 Upwork-style job posts (fictional clients) with the answer key."""

from __future__ import annotations

import json
from pathlib import Path

TODAY = "2026-10-12"

PROFILE_TOML = """# A fictional freelancer for the upworkbid example; replace with your own.
[profile]
name = "Sam Okafor"
headline = "AI engineer: agents, RAG and document automation in Python"
hourly_rate = 110
floor_after_fees = 90          # the kill rule: an offer must work out at $90/h or more after Upwork's fee
fee = 0.10
min_fixed_budget = 500
max_proposals = "20 to 50"     # skip jobs that already have more proposals than this
skills = ["python", "openai", "langchain", "llm", "rag", "agents", "hubspot", "shopify", "pdf", "fastapi", "aws", "ocr"]
exclude_words = ["data entry", "wordpress", "logo design"]
proposal_words = 150
signature = "Sam"

[[portfolio]]
id = "P1"
title = "Lead-research agent"
summary = "A Python agent that reads company websites, scores each company against an ideal customer profile and writes a cited opening line; tuned over 3 real runs."
link = "https://portfolio.example/lead-agent"
tags = ["agents", "sales", "llm"]

[[portfolio]]
id = "P2"
title = "Security questionnaire drafting"
summary = "Drafts answers to 250-question security questionnaires from a company's own documents, citing a source for every answer, with code checks on numbers and claims."
link = "https://portfolio.example/security-questionnaires"
tags = ["rag", "documents"]

[[portfolio]]
id = "P3"
title = "Wholesale order agent for Shopify"
summary = "Turns emailed purchase orders (PDF and Excel) into Shopify draft orders, matching every line to the catalog and checking prices and totals in code."
link = "https://portfolio.example/shopify-orders"
tags = ["pdf", "extraction", "shopify"]

[[project_type]]
name = "simple automation"
hours = [6, 10]

[[project_type]]
name = "document extraction pipeline"
hours = [15, 25]

[[project_type]]
name = "RAG chatbot"
hours = [20, 35]

[[project_type]]
name = "agent with integrations"
hours = [20, 30]

[[project_type]]
name = "model fine-tuning"
hours = [30, 60]
"""


def _post(title, body, budget, skills, client, activity, questions=()) -> str:
    q = ""
    if questions:
        q = ("You will be asked to answer the following questions when submitting a proposal:\n"
             + "\n".join(f"{i}. {x}" for i, x in enumerate(questions, 1)) + "\n\n")
    return (f"{title}\n\nPosted 3 hours ago\nWorldwide\n\n{body}\n\n{budget}\n\nIntermediate\nExperience Level\n\n"
            f"Skills and Expertise: {skills}\n\n{q}Activity on this job\n{activity}\n\nAbout the client\n{client}\n")


GOOD_CLIENT = ("Payment method verified\n4.9 of 5 reviews\nUnited States\n18 jobs posted\n80% hire rate, 2 open jobs\n"
               "$45K total spent\nMember since Mar 15, 2019")

JOBS = [
    dict(file="job01_lead_agent.txt", expect="bid", rate=108, text=_post(
        "Build an AI agent to qualify inbound leads and update HubSpot",
        "We get about 200 inbound demo requests a month through a HubSpot form. We want an AI agent that researches "
        "each company, scores it against our ideal customer profile, writes a short summary and updates the HubSpot "
        "record, so our two sales reps only call the best leads.\n\nDeliverables:\n- Agent that runs on each new form "
        "submission\n- Company research from the website\n- Score and summary in custom HubSpot properties\n- Short "
        "handover doc",
        "$3,000.00\nFixed-price", "Python, LangChain, HubSpot, OpenAI API", GOOD_CLIENT,
        "Proposals: 10 to 15\nInterviewing: 1\nSend a proposal for: 16 Connects",
        ["Which CRM have you connected an AI agent to before?"])),
    dict(file="job02_shopify_chatbot.txt", expect="skip", reason="pays up to $40/h", text=_post(
        "Chatbot for my Shopify store", "Looking for someone to set up an AI chatbot that answers customer questions "
        "about shipping and returns on our Shopify store.", "$25.00 - $40.00\nHourly", "Shopify, Chatbot, OpenAI API",
        GOOD_CLIENT, "Proposals: 5 to 10\nSend a proposal for: 10 Connects")),
    dict(file="job03_news_digest.txt", expect="maybe", rate=90, text=_post(
        "Python script to summarize industry news every morning",
        "I run a small consultancy and want a Python script to summarize the top stories in our industry every "
        "morning from a list of news sites and RSS feeds, and email me the digest.",
        "$800.00\nFixed-price", "Python, Web Scraping, OpenAI API",
        "Payment method verified\nNo reviews yet\nUnited States\n1 job posted\n0% hire rate, 1 open job\n"
        "Member since Sep 28, 2026", "Proposals: 5 to 10\nSend a proposal for: 8 Connects")),
    dict(file="job04_policy_rag.txt", expect="bid", rate=108, text=_post(
        "RAG assistant over our HR policy PDFs",
        "We want an internal assistant that can answer employee questions from our 300 policy PDFs, always showing "
        "which policy the answer came from. It must say when it doesn't know. Hosting on AWS.",
        "$80.00 - $120.00\nHourly\nMore than 30 hrs/week\n1 to 3 months", "Python, RAG, LangChain, AWS",
        "Payment method verified\n4.8 of 5 reviews\nUnited States\n25 jobs posted\n70% hire rate, 1 open job\n"
        "$120K total spent\nMember since Jun 2, 2018", "Proposals: 5 to 10\nSend a proposal for: 12 Connects",
        ["How do you stop the assistant from making things up?", "Which vector database would you use and why?"])),
    dict(file="job05_telegram.txt", expect="skip", reason="off Upwork", text=_post(
        "Simple data entry, quick money",
        "Easy job for the right person. Contact me on Telegram @fastjobs99 for details, we pay outside Upwork weekly.",
        "$50.00\nFixed-price", "Data Entry", "Payment method verified\nNo reviews yet\nMember since Oct 1, 2026",
        "Proposals: 5 to 10\nSend a proposal for: 4 Connects")),
    dict(file="job06_unverified.txt", expect="skip", reason="payment method not verified", text=_post(
        "LangChain expert for a long-term AI product",
        "Long-term work on our AI product. Must know LangChain, agents and evaluation.", "$60.00 - $85.00\nHourly",
        "Python, LangChain, LLM", "Payment method not verified\nNo reviews yet\nMember since Aug 12, 2026",
        "Proposals: 15 to 20\nSend a proposal for: 12 Connects")),
    dict(file="job07_finetune.txt", expect="skip", reason="50+ proposals", text=_post(
        "Fine-tune an LLM for legal document classification",
        "Fine-tune an open model to classify contracts into 40 categories. We have 5,000 labeled examples.",
        "$5,000.00\nFixed-price", "Python, LLM, Fine-tuning", GOOD_CLIENT,
        "Proposals: 50+\nSend a proposal for: 16 Connects")),
    dict(file="job08_invoice_pdf.txt", expect="bid", rate=90, text=_post(
        "Automate invoice extraction from PDFs into QuickBooks",
        "Our bookkeeper types about 300 supplier invoices a month into QuickBooks Online. We want a tool to extract "
        "vendor, date, line items and totals from PDF invoices and create bills in QuickBooks Online, with a review "
        "step for anything it isn't sure about.",
        "$2,000.00\nFixed-price", "Python, PDF, OCR, QuickBooks",
        "Payment method verified\n4.7 of 5 reviews\nUnited States\n10 jobs posted\n60% hire rate, 1 open job\n"
        "$8K total spent\nMember since Jan 20, 2021", "Proposals: 15 to 20\nSend a proposal for: 14 Connects",
        ["Have you worked with the QuickBooks Online API?"])),
]


def _q(point: str, quote: str) -> dict:
    return {"point": point, "quote": quote}


# What a perfect model answers for the jobs the rules leave in.
ASSESS = {
    "job01_lead_agent.txt": dict(
        project_type="agent with integrations", fit=90,
        requirements=[_q("research and score each lead", "researches each company, scores it against our ideal "
                                                         "customer profile"),
                      _q("write to HubSpot", "updates the HubSpot record")],
        red_flags=[], opening_detail="about 200 inbound demo requests a month",
        proposal="With about 200 inbound demo requests a month, your two reps should only see the best leads first. "
                 "I built a lead-research agent (P1) that reads company websites, scores each company against an "
                 "ideal customer profile and writes a cited summary; it was tuned over 3 real runs. For you I'd "
                 "trigger it on each new HubSpot form submission and write the score and summary to custom "
                 "properties. First step: a short call to agree the scoring rubric and the properties, then a "
                 "working version on a sample of your past leads.",
        portfolio_used=["P1"],
        answers=["My lead-research agent (P1) works from exports today; writing its scores to HubSpot through the "
                 "API is the part I'd add for you, using custom properties."]),
    "job03_news_digest.txt": dict(
        project_type="simple automation", fit=70,
        requirements=[_q("daily summary by email", "summarize the top stories in our industry every morning")],
        red_flags=[], opening_detail="summarize the top stories in our industry every morning",
        proposal="A script to summarize the top stories in your industry every morning is a small, well-defined "
                 "job. I'd read the sites and RSS feeds you list, summarize each story in a couple of lines with its "
                 "link, and email you the digest at a time you choose. My lead-research agent (P1) does the same "
                 "kind of reading and summarizing for company websites. Which sources matter most to you?",
        portfolio_used=["P1"], answers=[]),
    "job04_policy_rag.txt": dict(
        project_type="RAG chatbot", fit=92,
        requirements=[_q("answers from policy PDFs with sources", "answer employee questions from our 300 policy "
                                                                  "PDFs"),
                      _q("admit when it doesn't know", "It must say when it doesn't know.")],
        red_flags=[], opening_detail="answer employee questions from our 300 policy PDFs",
        proposal="Answering employee questions from your 300 policy PDFs, always with the source, is close to what "
                 "I built for security questionnaires (P2): it drafts answers from a company's own documents, cites a "
                 "source for every answer and checks numbers and claims in code, so unsupported answers are flagged "
                 "instead of shown. For you I'd add a simple chat page on AWS and an \"I don't know\" path when no "
                 "policy covers the question. First step: share a few sample PDFs and real questions, and I'll show "
                 "you answers with their citations.",
        portfolio_used=["P2"],
        answers=["Every answer must cite the passage it came from, and code checks that numbers and claims appear in "
                 "the cited text; if nothing supports an answer, it says it doesn't know.",
                 "For a few hundred PDFs, keyword search plus a small embedding index in Postgres (pgvector) is "
                 "enough and cheap to run on AWS; a dedicated vector database only pays off for a much larger "
                 "collection."]),
    "job08_invoice_pdf.txt": dict(
        project_type="document extraction pipeline", fit=85,
        requirements=[_q("extract invoice fields", "extract vendor, date, line items and totals from PDF invoices"),
                      _q("create bills in QuickBooks", "create bills in QuickBooks Online")],
        red_flags=[], opening_detail="extract vendor, date, line items and totals from PDF invoices",
        proposal="To extract vendor, date, line items and totals from PDF invoices and create bills in QuickBooks "
                 "Online, I'd reuse the approach of my wholesale order agent (P3): it turns emailed PDF and Excel "
                 "purchase orders into Shopify draft orders and checks every price and total in code. For your "
                 "invoices the same checks apply (line totals must add up, the vendor must exist) before anything "
                 "reaches QuickBooks, and anything uncertain goes to your bookkeeper's review list. First step: a "
                 "handful of sample invoices so I can show you the extracted fields side by side.",
        portfolio_used=["P3"],
        answers=["Not in a delivered project yet. My order agent (P3) posts to the Shopify Admin API the same way, "
                 "and QuickBooks Online's API for bills follows a similar pattern."]),
}


def make_sample(out: Path) -> dict:
    """Writes profile.toml, jobs/ and truth.json; returns the answer key."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "profile.toml").write_text(PROFILE_TOML, encoding="utf-8")
    (out / "jobs").mkdir(exist_ok=True)
    for j in JOBS:
        (out / "jobs" / j["file"]).write_text(j["text"], encoding="utf-8")
    truth = {"note": "Answer key for the synthetic Upwork posts made by `python -m upworkbid sample`. Fictional "
                     f"clients. Run with --today {TODAY}. `assess` is what a perfect model returns.",
             "today": TODAY, "assess": ASSESS,
             "jobs": [{"file": j["file"], "decision": j["expect"], "reason": j.get("reason", ""),
                       "rate": j.get("rate")} for j in JOBS]}
    (out / "truth.json").write_text(json.dumps(truth, indent=1) + "\n", encoding="utf-8")
    return truth


# --- scoring a run against the answer key -------------------------------------------------------------------------

def score(results: dict, truth: dict) -> dict:
    pred = {j["file"]: j for j in results["jobs"]}
    out = {"jobs": 0, "decision_right": 0, "wrong_decisions": [], "reason_right": 0, "reason_expected": 0,
           "rate_right": 0, "rate_expected": 0, "wrong_rates": [], "ready": 0, "bids": 0}
    for t in truth["jobs"]:
        p = pred.get(t["file"], {})
        out["jobs"] += 1
        if p.get("decision") == t["decision"]:
            out["decision_right"] += 1
        else:
            out["wrong_decisions"].append(f"{t['file']}: {p.get('decision', 'missing')} (expected {t['decision']})")
        if t["reason"]:
            out["reason_expected"] += 1
            out["reason_right"] += t["reason"] in p.get("reasons", "")
        if t["rate"] is not None:
            out["rate_expected"] += 1
            got = p.get("rate_mid")
            if got is not None and abs(got - t["rate"]) < 1:
                out["rate_right"] += 1
            else:
                out["wrong_rates"].append(f"{t['file']}: {got} (expected {t['rate']})")
        if t["decision"] == "bid":
            out["bids"] += 1
            out["ready"] += bool(p.get("ready"))
    return out


def _pct(a: int, b: int) -> str:
    return f"{a} of {b}" + (f" ({100 * a / b:.0f}%)" if b else "")


def format_score(s: dict) -> str:
    lines = [f"Jobs decided right (bid / maybe / skip): {_pct(s['decision_right'], s['jobs'])}"]
    lines += [f"  {w}" for w in s["wrong_decisions"]]
    lines.append(f"Skipped for the right reason: {_pct(s['reason_right'], s['reason_expected'])}")
    lines.append(f"Rates after fees right: {_pct(s['rate_right'], s['rate_expected'])}")
    lines += [f"  {w}" for w in s["wrong_rates"]]
    lines.append(f"Proposals ready to send for the jobs to bid on: {_pct(s['ready'], s['bids'])}")
    return "\n".join(lines)
