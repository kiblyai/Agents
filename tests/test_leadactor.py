import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

from leadactor.__main__ import cost_line, cost_report
from leadactor.actor import (DEFAULT_SIGNALS, EVENT, InputError, SetupError, check_website, execute, item_facts,
                             parse_input, summary)
from leadactor.platforms import ApifyPlatform, LocalPlatform
from leadagent.config import Settings
from leadagent.llm import LLM

from .fakes import FakeClient, as_json, no_sleep, site_transport, user_prompt

ROOT = Path(__file__).resolve().parents[1]

SITES = {
    "https://acme.com/": (200, '<title>Acme</title><p>Payroll software for dental clinics.</p>'
                               '<a href="/careers">Careers</a>'),
    "https://acme.com/careers": (200, "<p>We are hiring a Head of Growth to lead marketing in the US.</p>"),
    "https://bolt.io/": (200, "<title>Bolt</title><p>Scheduling software for gyms.</p>"),
    "https://meh.com/": (200, "<title>Meh</title><p>A bakery in Leeds.</p>"),
}
ACME = {
    "company_name": "Acme", "summary": "Payroll software for dental clinics.", "fits_icp": True, "score": 8,
    "criteria": [{"name": "industry", "status": "met", "evidence": "B2B software"}],
    "signals": [
        {"type": "hiring", "headline": "Hiring a Head of Growth", "evidence": "hiring a Head of Growth to lead marketing",
         "source_url": "https://acme.com/careers", "date": "2026-08"},
        {"type": "funding", "evidence": "raised a $40M Series B", "source_url": "https://acme.com/careers"},
    ],
    "why_now_index": 0,
}
BOLT = {"company_name": "Bolt", "summary": "Scheduling software for gyms.", "fits_icp": True, "score": 7,
        "signals": [], "why_now_index": None}
MEH = {"company_name": "Meh", "summary": "Bakery.", "fits_icp": False, "score": 1, "signals": []}
INPUT = {"idealCustomer": "B2B software companies hiring growth people", "websites": ["acme.com", "meh.com"]}


def responder(kwargs):
    prompt = user_prompt(kwargs)
    for domain, research in (("acme.com", ACME), ("bolt.io", BOLT), ("meh.com", MEH)):
        if f"COMPANY DOMAIN: {domain}" in prompt:
            return as_json(research)
    if prompt.startswith("Company: "):
        return as_json({"line": "Your new Head of Growth role usually means outbound pipeline is next on the list."})
    raise AssertionError("unexpected prompt")


def _execute(platform, *, client=None, model="paid/model", sites=SITES, concurrency=1, **settings_kw):
    settings = Settings(api_key="x", model=model, rpm=0, concurrency=concurrency, **settings_kw)
    llm = LLM(client or FakeClient(responder), model, rpm=0, sleep=no_sleep)

    async def go():
        async with httpx.AsyncClient(transport=site_transport(sites)) as http:
            return await execute(platform, settings, llm=llm, http=http, progress=lambda _: None)

    outcome, job, llm = asyncio.run(go())
    return outcome, job, llm


# --- input ---------------------------------------------------------------------------------------------------------

def test_input_websites_are_cleaned_deduplicated_and_limited():
    job = parse_input({"idealCustomer": "B2B SaaS", "maxCompanies": 2,
                       "websites": ["https://www.Acme.com/about", "acme.com", "bolt.io, meh.com", "not a site",
                                    "linkedin.com/company/acme", {"url": "https://zed.dev"}]})
    assert [c[0] for c in job.companies] == ["acme.com", "bolt.io"]
    assert job.over_limit == 2  # meh.com and zed.dev
    reasons = {r["website"]: r["reason"] for r in job.rejected}
    assert reasons == {"not": "not a website or domain", "a": "not a website or domain",
                       "site": "not a website or domain",
                       "linkedin.com": "linkedin.com is a listing or social profile, not the company's own website"}
    assert job.icp.signals == DEFAULT_SIGNALS and job.icp.min_score == 6 and job.write_lines


def test_input_from_another_actors_dataset_uses_website_and_list_data():
    # Google Maps Scraper items: `url` is the Maps link, `website` the business's own site
    items = [
        {"title": "Acme Dental Payroll", "website": "https://acme.com/", "url": "https://www.google.com/maps/place/x",
         "countryCode": "US", "categoryName": "Software company", "totalScore": 4.8},
        {"title": "No Site Cafe", "url": "https://www.google.com/maps/place/y"},
        {"title": "Only Maps", "phone": "123"},
        {"companyName": "Bolt", "domain": "bolt.io", "employeeCount": 42, "Latest Funding": "Seed"},
    ]
    job = parse_input({"idealCustomer": "B2B SaaS", "datasetId": "abc"}, items)
    assert job.companies == [
        ("acme.com", "Acme Dental Payroll", {"Industry": "Software company", "Country": "US"}),
        ("bolt.io", "Bolt", {"Employees": "42", "Funding stage": "Seed"}),
    ]
    assert [r["reason"] for r in job.rejected] == [
        "google.com is a listing or social profile, not the company's own website", "no website, domain or url field"]


def test_dataset_input_is_read_through_the_platform(tmp_path):
    items = [{"title": "Acme", "website": "acme.com", "reviews": [{"text": "x" * 1000}] * 50, "employeeCount": 12},
             {"title": "Meh", "website": "https://meh.com/"}]
    (tmp_path / "maps.json").write_text(json.dumps(items))
    platform = LocalPlatform({"idealCustomer": "B2B software", "datasetId": str(tmp_path / "maps.json")}, tmp_path)
    outcome, job, _ = _execute(platform)
    assert job.companies == [("acme.com", "Acme", {"Employees": "12"}), ("meh.com", "Meh", {})]
    assert outcome.scored == 2
    missing = LocalPlatform({"idealCustomer": "B2B software", "datasetId": "nope.json"}, tmp_path)
    with pytest.raises(InputError, match="Could not read dataset nope.json"):
        _execute(missing)


def test_input_builds_the_target_customer_spec():
    job = parse_input({"idealCustomer": "Clinics software", "industries": ["healthtech"], "minEmployees": 10,
                       "fundingStages": "Seed", "countries": ["UK"], "buyingSignals": [], "exclude": ["agencies"],
                       "minScore": 7, "requireSizeOrStage": False, "offer": "lead lists", "writeOpeningLines": False,
                       "maxPagesPerCompany": 2, "websites": ["acme.com"]})
    icp = job.icp
    assert (icp.description, icp.industries, icp.employee_range, icp.stages) == (
        "Clinics software", ["healthtech"], [10, 1_000_000], ["Seed"])
    assert (icp.geographies, icp.signals, icp.exclude, icp.min_score, icp.require_size_or_stage) == (
        ["UK"], [], ["agencies"], 7, False)
    assert job.writer_cfg.offer == "lead lists" and not job.write_lines and job.max_pages == 2


@pytest.mark.parametrize("data, message", [
    ({"websites": ["acme.com"]}, "Who you sell to"),
    ({"idealCustomer": "x"}, "No company websites"),
    ({"idealCustomer": "x", "websites": ["linkedin.com"]}, "listing or social profile"),
    ({"idealCustomer": "x", "websites": ["a.com"], "minEmployees": 50, "maxEmployees": 10}, "larger than"),
    ({"idealCustomer": "x", "websites": ["a.com"], "minScore": 11}, "between 0 and 10"),
    ({"idealCustomer": "x", "websites": ["a.com"], "maxCompanies": "lots"}, "whole number"),
])
def test_bad_input_is_explained(data, message):
    with pytest.raises(InputError, match=message):
        parse_input(data)


def test_check_website():
    assert check_website("HTTPS://Acme.co.uk/pricing") == ("acme.co.uk", "")
    assert check_website("maps.google.co.uk")[1].endswith("own website")
    assert check_website("facebook.com/acme")[1].endswith("own website")
    assert check_website("localhost") == ("", "not a website or domain")
    assert item_facts({"Company Country": "Canada", "# Employees": 12, "nested": {"x": 1}, "flag": True}) == {
        "Employees": "12", "Country": "Canada"}


# --- running and charging --------------------------------------------------------------------------------------------

def test_one_row_and_one_charge_per_scored_company(tmp_path):
    platform = LocalPlatform({**INPUT, "websites": ["acme.com", "meh.com", "deadco.com"]}, tmp_path)
    outcome, job, llm = _execute(platform)
    rows = [json.loads(line) for line in (tmp_path / "dataset.jsonl").read_text().splitlines()]
    assert [r["website"] for r in rows] == ["acme.com", "meh.com"]  # the unreachable site is not a row
    acme, meh = rows
    assert acme["qualified"] and acme["score"] == 8 and acme["companyName"] == "Acme"
    assert acme["whyNow"] == "Hiring a Head of Growth (2026-08)"
    assert acme["whyNowSource"] == "https://acme.com/careers"
    assert acme["openingLine"].startswith("Your new Head of Growth") and acme["openingLineStatus"] == "ok"
    # the Series B claim is not on the cited page, so it is not shown
    assert [s["type"] for s in acme["signals"]] == ["hiring"] and not acme["signals"][0]["old"]
    assert not meh["qualified"] and meh["openingLine"] == "" and meh["openingLineStatus"] == "not_written"
    assert platform.charged == 2
    assert outcome.not_scored == [{"website": "deadco.com", "reason": "website could not be read (offline, blocked, "
                                                                      "or its robots.txt disallows it)"}]
    assert llm.usage.requests == 3  # 2 research calls + 1 opening line

    stats = summary(outcome, job)
    assert (stats["scored"], stats["qualified"], stats["notFit"], stats["notScored"]) == (2, 1, 1, 1)
    asyncio.run(platform.save_summary(stats))
    assert (tmp_path / "results.csv").read_text().splitlines()[1].startswith("acme.com,Acme,True,8,")


def test_no_work_starts_that_the_users_maximum_charge_cannot_pay_for(tmp_path):
    websites = ["deadco.com", "acme.com", "bolt.io", "meh.com"]
    platform = LocalPlatform({**INPUT, "websites": websites}, tmp_path, price=0.03, max_charge=0.07)
    client = FakeClient(responder)
    outcome, _, _ = _execute(platform, client=client)
    assert platform.charged == 2  # $0.06; a third would cost $0.09
    assert outcome.scored == 2 and outcome.not_started == 1 and "maximum charge" in outcome.stopped
    research_calls = [c for c in client.calls if "COMPANY DOMAIN" in user_prompt(c)]
    assert len(research_calls) == 2  # meh.com was never researched, so no unbilled model cost
    assert outcome.not_scored[0]["website"] == "deadco.com"  # unreachable sites don't use up the budget


class SlowPushPlatform(LocalPlatform):
    """Storing a row takes a moment, as it does on Apify, so other companies run meanwhile."""

    async def push(self, item):
        await asyncio.sleep(0)
        return await super().push(item)


def test_parallel_companies_never_overrun_the_budget(tmp_path):
    websites = ["acme.com", "bolt.io", "meh.com", "deadco.com"]
    platform = SlowPushPlatform({**INPUT, "websites": websites}, tmp_path, price=0.03, max_charge=0.07)
    client = FakeClient(responder)
    outcome, _, _ = _execute(platform, client=client, concurrency=3)
    research_calls = [c for c in client.calls if "COMPANY DOMAIN" in user_prompt(c)]
    assert platform.charged == 2 and len(research_calls) == 2 and outcome.scored == 2


def test_budget_below_one_company_does_no_work(tmp_path):
    platform = LocalPlatform(INPUT, tmp_path, price=0.03, max_charge=0.02)
    client = FakeClient(responder)
    outcome, _, _ = _execute(platform, client=client)
    assert outcome.scored == 0 and outcome.not_started == 2 and not client.calls


def test_a_restarted_run_skips_companies_already_in_its_dataset(tmp_path):
    first = LocalPlatform(INPUT, tmp_path)
    _execute(first)
    again = LocalPlatform(INPUT, tmp_path, resume=True)
    client = FakeClient(lambda kw: (_ for _ in ()).throw(AssertionError("should be skipped")))
    outcome, _, _ = _execute(again, client=client)
    assert outcome.already_done == 2 and outcome.scored == 0 and again.charged == 2
    assert len((tmp_path / "dataset.jsonl").read_text().splitlines()) == 2
    fresh = LocalPlatform(INPUT, tmp_path)  # without --resume a local run starts over
    assert not (tmp_path / "dataset.jsonl").exists() and fresh.charged == 0


def test_model_errors_are_free_and_the_run_continues(tmp_path):
    def broken_for_acme(kwargs):
        return "sorry" if "COMPANY DOMAIN: acme.com" in user_prompt(kwargs) else responder(kwargs)

    platform = LocalPlatform(INPUT, tmp_path)
    outcome, _, _ = _execute(platform, client=FakeClient(broken_for_acme))
    assert platform.charged == 1 and outcome.scored == 1
    assert outcome.not_scored[0]["website"] == "acme.com" and "LLMOutputError" in outcome.not_scored[0]["reason"]


def test_users_on_apify_never_see_the_model_name(tmp_path):
    def broken_for_acme(kwargs):
        return "sorry" if "COMPANY DOMAIN: acme.com" in user_prompt(kwargs) else responder(kwargs)

    logs = []
    settings = Settings(api_key="x", model="secret-vendor/model-x", rpm=0, concurrency=1)
    llm = LLM(FakeClient(broken_for_acme), "secret-vendor/model-x", rpm=0, sleep=no_sleep)

    async def go():
        async with httpx.AsyncClient(transport=site_transport(SITES)) as http:
            return await execute(LocalPlatform(INPUT, tmp_path), settings, llm=llm, http=http, progress=logs.append,
                                 show_errors=False)

    outcome, job, _ = asyncio.run(go())
    assert outcome.not_scored[0]["reason"] == "temporary error while scoring; try this company again later"
    seen = json.dumps(summary(outcome, job)) + "\n".join(logs) + (tmp_path / "dataset.jsonl").read_text()
    assert "secret-vendor" not in seen and "model-x" not in seen


# --- setup checks ----------------------------------------------------------------------------------------------------

class PaidPlatform(LocalPlatform):
    paid = True


@pytest.mark.parametrize("settings_kw", [
    {"model": "openrouter/free"},
    {"model": "nvidia/nemotron-3-super-120b-a12b:free"},
    {"fallback_models": ["google/gemma-4-31b-it:free"]},
    {"writer_model": "openrouter/free"},
])
def test_paid_runs_refuse_free_models(tmp_path, settings_kw):
    client = FakeClient(responder)
    with pytest.raises(SetupError, match="free models"):
        _execute(PaidPlatform(INPUT, tmp_path), client=client, **settings_kw)
    assert not client.calls
    _execute(LocalPlatform(INPUT, tmp_path), **settings_kw)  # a local test run may use them


def test_missing_key_is_a_setup_error(tmp_path):
    settings = Settings(api_key="", model="paid/model")
    with pytest.raises(SetupError, match="OPENROUTER_API_KEY"):
        asyncio.run(execute(LocalPlatform(INPUT, tmp_path), settings))


# --- the Apify side ----------------------------------------------------------------------------------------------------

class FakeCharging:
    def __init__(self, ppe: bool, prices: dict, limit: int):
        self.info = type("Info", (), {"is_pay_per_event": ppe, "per_event_prices": prices})()
        self.limit = limit

    def get_pricing_info(self):
        return self.info

    def compute_push_data_limit(self, items_count, event_name, *, is_default_dataset):
        assert event_name == EVENT and is_default_dataset
        return min(items_count, self.limit)


def test_apify_platform_pricing_checks():
    def platform(ppe, prices, limit=10**12):
        actor = type("A", (), {"get_charging_manager": lambda self: FakeCharging(ppe, prices, limit)})()
        return ApifyPlatform(actor)

    assert "no priced 'company-scored'" in platform(True, {"other": 1}).pricing_problem()
    assert "no priced" in platform(True, {EVENT: 0}).pricing_problem()
    assert platform(True, {EVENT: 0.03}, limit=3).chargeable() == 3
    assert platform(True, {EVENT: 0.03}).chargeable() is None
    unpaid = platform(False, {})
    assert unpaid.pricing_problem() == "" and unpaid.chargeable() is None and not unpaid.paid


def test_apify_sdk_charges_per_row_and_stops_at_the_users_limit(tmp_path, monkeypatch):
    """The real Apify SDK, run locally: pay-per-event at $0.03 with a $0.07 limit.

    Off the platform the SDK prices unknown events at $1, so Apify's own per-row event is set to $0 here, as it is
    on the platform when left unpriced.
    """
    apify = pytest.importorskip("apify")
    monkeypatch.setenv("APIFY_LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("APIFY_ACTOR_PRICING_INFO", json.dumps({
        "pricingModel": "PAY_PER_EVENT",
        "pricingPerEvent": {"actorChargeEvents": {
            EVENT: {"eventPriceUsd": 0.03, "eventTitle": "Company scored"},
            "apify-default-dataset-item": {"eventPriceUsd": 0, "eventTitle": "Dataset item"}}}}))
    monkeypatch.setenv("APIFY_CHARGED_ACTOR_EVENT_COUNTS", json.dumps({EVENT: 0}))
    monkeypatch.setenv("ACTOR_MAX_TOTAL_CHARGE_USD", "0.07")
    monkeypatch.setenv("APIFY_PURGE_ON_START", "0")
    store = tmp_path / "storage" / "key_value_stores" / "default"
    store.mkdir(parents=True)
    (store / "INPUT.json").write_text(json.dumps({**INPUT, "websites": ["deadco.com", "acme.com", "bolt.io", "meh.com"]}))

    async def go():
        async with apify.Actor(exit_process=False) as actor:
            platform = ApifyPlatform(actor)
            assert platform.paid and platform.pricing_problem() == ""
            maps = await actor.open_dataset(name="maps-results")
            await maps.push_data([{"title": "Acme", "website": "acme.com"}, {"title": "Meh", "url": "meh.com"}])
            assert [i["title"] async for i in platform.read_dataset(maps.id)] == ["Acme", "Meh"]
            async with httpx.AsyncClient(transport=site_transport(SITES)) as http:
                settings = Settings(api_key="x", model="paid/model", rpm=0, concurrency=2)
                llm = LLM(FakeClient(responder), "paid/model", rpm=0, sleep=no_sleep)
                outcome, job, _ = await execute(platform, settings, llm=llm, http=http, progress=lambda _: None)
            await platform.save_summary(summary(outcome, job))
            charged = actor.get_charging_manager().get_charged_event_count(EVENT)
            return outcome, charged, await platform.done_items(), await actor.get_value("OUTPUT")

    outcome, charged, rows, saved = asyncio.run(go())
    assert charged == 2 and len(rows) == 2 and outcome.scored == 2
    assert outcome.not_started == 1 and "maximum charge" in outcome.stopped
    assert saved["notScoredList"][0]["website"] == "deadco.com"


# --- files Apify reads -----------------------------------------------------------------------------------------------

def _actor_file(name: str) -> dict:
    return json.loads((ROOT / ".actor" / name).read_text())


def test_input_schema_matches_the_code():
    schema = _actor_file("input_schema.json")
    props = schema["properties"]
    source = (ROOT / "leadactor" / "actor.py").read_text()
    read_by_code = re.findall(r'\.get\("(\w+)"\)|_(?:int|bool|strings)\(data, "(\w+)"|"(\w+)" in data', source)
    assert set(props) == {next(k for k in keys if k) for keys in read_by_code}
    assert props["buyingSignals"]["default"] == DEFAULT_SIGNALS
    assert props["minScore"]["default"] == 6 and props["maxCompanies"]["default"] == 100
    assert props["maxPagesPerCompany"]["default"] == 4
    assert props["requireSizeOrStage"]["default"] is True and props["writeOpeningLines"]["default"] is True
    assert schema["required"] == ["idealCustomer"]
    # the prefilled example runs as-is
    job = parse_input({k: v.get("prefill", v.get("default")) for k, v in props.items()
                       if "prefill" in v or "default" in v})
    assert len(job.companies) == 3


def test_dataset_views_show_fields_the_rows_have(tmp_path):
    platform = LocalPlatform(INPUT, tmp_path)
    _execute(platform)
    row = json.loads((tmp_path / "dataset.jsonl").read_text().splitlines()[0])
    for view in _actor_file("dataset_schema.json")["views"].values():
        assert set(view["transformation"]["fields"]) <= set(row)
        assert set(view["display"]["properties"]) == set(view["transformation"]["fields"])


def test_actor_files_point_at_real_files_and_keep_keys_secret():
    actor = _actor_file("actor.json")
    for key in ("readme", "dockerfile", "inputSchema", "outputSchema"):
        assert (ROOT / ".actor" / actor[key]).is_file(), key
    assert (ROOT / ".actor" / actor["storages"]["dataset"]).is_file()
    assert (ROOT / ".actor" / actor["dockerContextDir"]).resolve() == ROOT
    env = actor["environmentVariables"]
    assert env["OPENROUTER_API_KEY"].startswith("@")  # an Apify secret, never a value in the repo
    dockerfile = (ROOT / ".actor" / "Dockerfile").read_text()
    for package in ("agentkit", "leadagent", "leadactor"):
        assert f"COPY --chown=myuser:myuser {package} ./{package}" in dockerfile
    assert ".env" not in dockerfile
    requirements = (ROOT / "leadactor" / "requirements.txt").read_text()
    for dep in ("apify", "openai", "httpx", "pydantic"):
        assert re.search(rf"^{dep}\b", requirements, re.M), dep
    ignored = (ROOT / ".gitignore").read_text().split()
    assert {".env", ".venv/", "storage/"} <= set(ignored)  # `apify push` uploads what git doesn't ignore


def test_cost_report_compares_model_cost_with_the_price():
    usage = type("U", (), {"cost_usd": 0.05, "models": {"m": 12}, "requests": 12})()
    report = cost_report(10, usage, "m", 0.03)
    assert report["modelCostPerScoredCompanyUsd"] == 0.005 and report["modelCostShareOfPrice"] == 0.167
    assert "17% of the $0.03 price" in cost_line(report) and "fine" in cost_line(report)
    assert "too high" in cost_line(cost_report(10, usage, "m", 0.01))
    usage.cost_usd = 0
    assert "free model" in cost_line(cost_report(10, usage, "m", None))
