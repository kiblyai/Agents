import asyncio
import csv

import httpx

from leadagent.config import ICP, Settings, WriterConfig
from leadagent.contacts import SyntaxOnlyVerifier
from leadagent.fetch import SiteReader
from leadagent.llm import LLM
from leadagent.models import Contact
from leadagent.output import LEAD_COLUMNS, lead_rows, qa_sample, write_csv
from leadagent.pipeline import run, summary

from .fakes import FakeClient, as_json, no_sleep, site_transport, user_prompt

SITES = {
    "https://acme.com/": (200, '<title>Acme</title><p>Payroll software for dental clinics.</p>'
                               '<a href="/careers">Careers</a><a href="/blog">Blog</a>'),
    "https://acme.com/careers": (200, "<p>We are hiring a Head of Growth to lead marketing in the US.</p>"),
    "https://acme.com/blog": (200, "<p>Acme raised a $12M Series A in August 2026.</p>"),
    "https://meh.com/": (200, "<title>Meh</title><p>A bakery in Leeds.</p>"),
}

ACME_RESEARCH = {
    "company_name": "Acme",
    "summary": "Payroll software for dental clinics.",
    "fits_icp": True,
    "score": 8,
    "score_reasons": ["B2B SaaS", "hiring growth lead"],
    "signals": [
        {"type": "hiring", "evidence": "hiring a Head of Growth to lead marketing", "source_url": "https://acme.com/careers"},
        {"type": "funding", "evidence": "raised a $12M Series A", "source_url": "https://acme.com/blog"},
        {"type": "launch", "evidence": "launched in Japan", "source_url": "https://acme.com/japan"},
    ],
    "why_now": "Hiring its first Head of Growth after a $12M Series A.",
    "disqualifiers": [],
}
MEH_RESEARCH = {"company_name": "Meh", "summary": "Bakery.", "fits_icp": False, "score": 1, "signals": [], "why_now": ""}


def responder(kwargs):
    prompt = user_prompt(kwargs)
    if "COMPANY DOMAIN: acme.com" in prompt:
        return "Here you go:\n```json\n" + as_json(ACME_RESEARCH) + "\n```"
    if "COMPANY DOMAIN: meh.com" in prompt:
        return as_json(MEH_RESEARCH)
    if "Company: Acme" in prompt:
        if "Your previous line was" in prompt:
            return as_json({"line": "Your new Head of Growth role after the Series A usually means outbound is next."})
        return as_json({"line": "I noticed you are hiring a Head of Growth!"})
    raise AssertionError("unexpected prompt")


ICP_SPEC = ICP(name="Seed-A SaaS hiring growth", description="B2B SaaS that just raised and is hiring for growth",
               target_titles=["Head of Growth", "CEO"], signals=["hiring growth roles", "recent funding"], min_score=6)


def _run(tmp_path, client):
    settings = Settings(api_key="x", rpm=0, concurrency=2)
    llm = LLM(client, "test-model", rpm=0, sleep=no_sleep)

    async def go():
        async with httpx.AsyncClient(transport=site_transport(SITES)) as http:
            reader = SiteReader(http)
            companies = [("acme.com", "Acme"), ("meh.com", "Meh"), ("deadco.com", "Dead Co")]
            return await run(companies, icp=ICP_SPEC, writer_cfg=WriterConfig(), settings=settings, llm=llm,
                             reader=reader, cache_dir=tmp_path / "cache", progress=lambda _: None)

    return asyncio.run(go()), llm


def test_end_to_end_and_resume_from_cache(tmp_path):
    client = FakeClient(responder)
    report, llm = _run(tmp_path, client)
    by = {r.domain: r for r in report.results}
    assert [r.domain for r in report.results] == ["acme.com", "meh.com", "deadco.com"]
    assert by["deadco.com"].status == "unreachable"
    assert by["meh.com"].status == "not_fit" and by["meh.com"].first_line_status == "skipped"

    acme = by["acme.com"]
    assert acme.status == "qualified"
    assert [s.type for s in acme.research.signals] == ["hiring", "funding"]  # made-up Japan page dropped
    assert all(s.verified for s in acme.research.signals)
    assert acme.first_line.startswith("Your new Head of Growth role") and acme.first_line_status == "ok"
    # 2 research calls + 2 writer calls (first line rejected once for banned phrase and "!")
    assert llm.usage.requests == 4

    contacts = {"acme.com": [Contact(domain="acme.com", first_name="Bo", title="Head of Growth", email="bo@acme.com"),
                             Contact(domain="acme.com", first_name="Cy", title="Engineer", email="cy@acme.com")]}
    rows = asyncio.run(lead_rows(report.results, contacts, ICP_SPEC.target_titles, 2, SyntaxOnlyVerifier()))
    assert len(rows) == 1 and rows[0]["contact_name"] == "Bo" and rows[0]["email_status"] == "unverified"
    assert "https://acme.com/careers" in rows[0]["sources"]
    write_csv(tmp_path / "leads.csv", rows, LEAD_COLUMNS)
    with open(tmp_path / "leads.csv") as f:
        assert next(csv.DictReader(f))["score"] == "8"
    assert qa_sample(rows) == rows

    stats = summary(report, llm.usage, 3)
    assert stats["status_counts"] == {"qualified": 1, "not_fit": 1, "unreachable": 1}
    assert stats["cost_usd"] > 0

    # second run: everything comes from cache, no model calls
    client2 = FakeClient(lambda kw: (_ for _ in ()).throw(AssertionError("should be cached")))
    report2, llm2 = _run(tmp_path, client2)
    assert report2.from_cache == 2 and llm2.usage.requests == 0  # unreachable site is simply re-tried
    assert {r.domain: r.status for r in report2.results}["deadco.com"] == "unreachable"


def test_bad_model_output_marks_company_error_and_run_continues(tmp_path):
    def broken_for_acme(kwargs):
        if "COMPANY DOMAIN: acme.com" in user_prompt(kwargs):
            return "sorry, I can't"
        return responder(kwargs)

    report, _ = _run(tmp_path, FakeClient(broken_for_acme))
    by = {r.domain: r for r in report.results}
    assert by["acme.com"].status == "error" and "LLMOutputError" in by["acme.com"].error
    assert by["meh.com"].status == "not_fit"
    assert not (tmp_path / "cache" / "acme.com.json").exists()  # errors are retried next run
