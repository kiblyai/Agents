import asyncio
import csv
import json
import re
from datetime import date
from pathlib import Path

import httpx
import pytest

from agentkit.llm import LLM
from agentkit.runner import Runner
from crmclean.config import CRMConfig, load_crm
from crmclean.enrich import Enrichment, check_enrichment, page_for
from crmclean.hubspot import HubSpot, apply, enum_value
from crmclean.output import approved, updates, write_imports, write_outputs
from crmclean.pipeline import CACHE_VERSION, clean, enrich
from crmclean.records import Record, read_export
from crmclean.rules import company_name_case, norm_domain, proper_name, us_phone
from crmclean.sample import make_sample, score

from .fakes import FakeClient, no_sleep, user_prompt

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "crmclean"
TODAY = date(2026, 10, 12)


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    out = tmp_path_factory.mktemp("crmclean")
    truth = make_sample(out)
    contacts, _ = read_export(out / "contacts.csv", "contact")
    companies, _ = read_export(out / "companies.csv", "company")
    return out, truth, load_crm(out / "crm.toml"), contacts, companies


def oracle(truth: dict):
    def respond(kwargs):
        name = re.search(r"^COMPANY: (.+?) \(", user_prompt(kwargs), re.M).group(1)
        return json.dumps(truth["enrich"][name])
    return respond


def _runner(tmp_path, responder):
    return Runner(LLM(FakeClient(responder), "test-model", rpm=0, sleep=no_sleep), tmp_path / "cache", CACHE_VERSION)


# --- formats ------------------------------------------------------------------------------------------------------

def test_formats_are_fixed_in_code():
    assert proper_name("MARY-KATE o'BRIEN") == "Mary-Kate O'Brien" and proper_name("mcdonald") == "McDonald"
    assert company_name_case("BRIGHTWAVE SOLAR LLC") == "Brightwave Solar LLC"
    assert company_name_case("ACME ANALYTICS INC") == "Acme Analytics Inc"
    assert company_name_case("SMITH AND JONES CPAS") == "Smith and Jones CPAS"
    assert us_phone("(512) 555-0182") == ("+1 512-555-0182", True)
    assert us_phone("1-512-555-0182 x204") == ("+1 512-555-0182 ext. 204", True)
    assert us_phone("+44 20 7946 0958") == ("+44 20 7946 0958", True)
    assert us_phone("555-0199") == ("555-0199", False)
    assert norm_domain("https://WWW.Acme.example/about?x=1") == "acme.example"


def test_exports_are_read_by_hubspot_column_names(case, tmp_path):
    _, _, _, contacts, companies = case
    assert len(contacts) == 12 and len(companies) == 10
    assert contacts[3].props["email"] == "priya.shah@acme-analytics.example "  # spaces kept: they're a finding
    assert companies[0].get("domain") == "northbeam.example" and companies[0].id == "1001"
    (tmp_path / "x.csv").write_text("First Name,Email\nA,a@b.co\n")
    with pytest.raises(ValueError, match="Record ID"):
        read_export(tmp_path / "x.csv", "contact")


# --- the whole example --------------------------------------------------------------------------------------------

def test_committed_example_matches_the_generator(tmp_path):
    truth = make_sample(tmp_path)
    assert truth == json.loads((EXAMPLE / "truth.json").read_text())
    for f in tmp_path.rglob("*"):
        if f.is_file():
            rel = f.relative_to(tmp_path)
            assert f.read_bytes() == (EXAMPLE / rel).read_bytes(), rel


def test_end_to_end_scores_full_marks(case, tmp_path):
    out, truth, cfg, contacts, companies = case
    res = clean(contacts, companies, cfg, TODAY)
    runner = _runner(tmp_path, oracle(truth))
    asyncio.run(enrich(res, companies, cfg, runner, out / "sites", progress=lambda _: None))
    assert runner.llm.usage.requests == 4 and res.no_page == ["Lakeshore Physical Therapy (lakeshore-pt.example)"]
    write_outputs(tmp_path, res)
    s = score(json.loads((tmp_path / "results.json").read_text()), truth)
    assert (s["changes_found"], s["changes_expected"], s["extra_changes"]) == (36, 36, [])
    assert (s["merges_found"], s["merges_expected"], s["extra_merges"]) == (2, 2, [])
    assert (s["flags_found"], s["flags_expected"], s["extra_flags"]) == (9, 9, [])

    with open(tmp_path / "changes.csv") as f:
        rows = list(csv.DictReader(f))
    assert {r["approve"] for r in rows if r["by"] == "rule"} == {"yes"}
    assert {r["approve"] for r in rows if r["by"] == "model"} == {""}  # website values wait for a person
    emp = next(r for r in rows if r["field"] == "numberofemployees")
    assert emp["new"] == "42" and emp["source"].startswith("northbeam.example.html (saved page): \"Our team of 42")
    with open(tmp_path / "merges.csv") as f:
        assert {r["approve"] for r in csv.DictReader(f)} == {""}  # merges can't be undone: always a person's call


def test_enrichment_needs_quotes_staff_words_and_known_industries(case):
    out, _, cfg, _, companies = case
    fern = next(c for c in companies if c.id == "1007")
    page, source = page_for("fernhill-bakery.example", out / "sites")
    assert source == "fernhill-bakery.example.html (saved page)" and "Family owned since 1987" in page
    e = Enrichment.model_validate({
        "industry": {"value": "Bakery", "quote": "delivers flour, sugar and baking equipment"},
        "description": {"value": "Serves 300 bakeries across the Northwest.", "quote": "to independent bakeries in "
                        "Oregon and Washington"},
        "employees": {"value": "1987", "quote": "Family owned since 1987"},
        "city": {"value": "Portland", "quote": "Our warehouse is in Portland"},
        "state": {"value": "OR", "quote": "based in Salem, Oregon"}})
    changes, flags = check_enrichment(e, fern, page, source, cfg)
    assert [(c.field, c.new) for c in changes] == [("state", "Oregon")]
    texts = [f.text for f in flags]
    assert "description mentions 300, which isn't on the page; left blank" in texts
    assert "employees '1987' is not a staff count in its quote; left blank" in texts
    assert "city 'Portland' dropped: its quote isn't on the page" in texts
    # industry is already set on this record, so it's not even considered
    nb = next(c for c in companies if c.id == "1001")
    _, flags2 = check_enrichment(Enrichment.model_validate({"industry": {"value": "Software", "quote": page.split(".")[0]}}),
                                 nb, page, source, cfg)
    assert [f.code for f in flags2] == ["unknown_industry"]


# --- after review -------------------------------------------------------------------------------------------------

def _fake_hubspot(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={"status": "COMPLETE"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_apply_sends_only_approved_rows_merges_first(case, tmp_path):
    out, truth, cfg, contacts, companies = case
    res = clean(contacts, companies, cfg, TODAY)
    asyncio.run(enrich(res, companies, cfg, _runner(tmp_path, oracle(truth)), out / "sites", progress=lambda _: None))
    write_outputs(tmp_path, res)
    # a person approves the website industry for Northbeam and the company merge, and rejects one rule fix
    rows = list(csv.DictReader(open(tmp_path / "changes.csv")))
    for r in rows:
        if r["record_id"] == "1001" and r["field"] == "industry":
            r["approve"] = "yes"
        if r["record_id"] == "203" and r["field"] == "phone":
            r["approve"] = "no"
    with open(tmp_path / "changes.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    merges = list(csv.DictReader(open(tmp_path / "merges.csv")))
    merges[0]["approve"] = "yes"
    with open(tmp_path / "merges.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(merges[0]))
        w.writeheader()
        w.writerows(merges)

    changes, merges_ok = approved(tmp_path)
    assert len(changes) == 20 and [m["merge id"] for m in merges_ok] == ["1003"]
    files = write_imports(tmp_path, changes)
    assert [f.name for f in files] == ["import_contacts.csv", "import_companies.csv", "associations.csv"]
    with open(tmp_path / "import_companies.csv") as f:
        imp = {r["Record ID"]: r for r in csv.DictReader(f)}
    assert imp["1001"]["Industry"] == "Computer Software" and imp["1002"]["Company name"] == "Acme Analytics Inc"

    calls = []
    props, links = updates(changes)
    done = apply(HubSpot("tok", client=_fake_hubspot(calls)), props, links, merges_ok)
    assert calls[0] == ("POST", "/crm/v3/objects/companies/merge", {"primaryObjectId": "1002", "objectIdToMerge": "1003"})
    batch = next(c for c in calls if c[1] == "/crm/v3/objects/companies/batch/update")[2]["inputs"]
    assert {"id": "1001", "properties": {"country": "United States", "industry": "COMPUTER_SOFTWARE"}} in batch
    assert ("PUT", "/crm/v4/objects/contact/204/associations/default/company/1002", None) in calls
    assert done == {"merges": 1, "contacts": 5, "companies": 5, "associations": 2}
    assert enum_value("Food & Beverages") == "FOOD_BEVERAGES"


# --- command line -------------------------------------------------------------------------------------------------

def test_cli_check_run_apply_and_score(tmp_path, monkeypatch, capsys):
    from crmclean import __main__ as cli

    ex = tmp_path / "ex"
    cli.main(["sample", "--out", str(ex)])
    truth = json.loads((ex / "truth.json").read_text())
    base = ["--contacts", str(ex / "contacts.csv"), "--companies", str(ex / "companies.csv"), "--crm",
            str(ex / "crm.toml"), "--today", "2026-10-12"]
    cli.main(["check", *base, "--out", str(tmp_path / "rules")])
    assert "Proposed: 20 fixes by rule, 0 values from websites, 2 merges; 9 records to look at" in capsys.readouterr().out
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(cli.LLM, "from_settings",
                        classmethod(lambda cls, s: LLM(FakeClient(oracle(truth)), s.model, rpm=0, sleep=no_sleep)))
    out = tmp_path / "out"
    cli.main(["run", *base, "--sites", str(ex / "sites"), "--out", str(out), "--model", "x:free"])
    printed = capsys.readouterr().out
    assert "WARNING: free models may log prompts" in printed
    assert "Proposed: 20 fixes by rule, 16 values from websites, 2 merges; 10 records to look at" in printed
    cli.main(["score", "--run", str(out), "--truth", str(ex / "truth.json")])
    report = capsys.readouterr().out
    assert "Expected changes proposed: 36 of 36 (100%); other changes: 0" in report
    cli.main(["apply", "--run", str(out)])
    printed = capsys.readouterr().out
    assert "Approved: 20 changes, 0 merges" in printed and "Dry run: nothing was sent" in printed
