"""Runs the lead agent as a pay-per-event Actor: one dataset row, and one charge, per company scored.

The rules that protect paying users and your margin are code, not settings:
- A company is charged only when its website was read and scored. Unreachable sites, model errors and unusable
  input rows are free: they go to the run summary instead of the dataset, so one row is always one charge.
- A company is only started while the user's maximum charge can still pay for it, so no work goes unbilled.
- A paying run refuses free models: they may log prompts and are capped at 50-1,000 requests a day.
- A restarted run skips companies already in its dataset, so nothing is charged twice.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Protocol

import httpx
import openai

from agentkit.llm import LLM, DailyLimitError
from leadagent.config import ICP, Settings, WriterConfig
from leadagent.contacts import FACT_COLUMNS
from leadagent.fetch import SiteReader, normalize_domain
from leadagent.models import CompanyResearch, CompanyResult
from leadagent.research import research_company
from leadagent.writer import write_first_line

EVENT = "company-scored"  # the pay-per-event event name; must match the one set in Apify Console
BUSY_LIMIT = 3  # consecutive companies failing on rate limits before the run stops
MAX_COMPANIES = 10_000
MAX_LISTED = 500  # unscored companies listed by name in the summary
MAX_MODEL_COST_SHARE = 0.25  # the business model assumes model costs stay under 25% of the price

DEFAULT_SIGNALS = [
    "hiring sales, marketing or growth roles",
    "raised funding in the last 12 months",
    "launched a new product or entered a new market",
    "new sales or marketing leader",
]
SCORED = ("qualified", "not_fit")
LINE_STATUS = {"ok": "ok", "needs_manual": "needs_edit", "skipped": "not_written"}
UNREACHABLE = "website could not be read (offline, blocked, or its robots.txt disallows it)"
MODEL_BUSY = "model busy (rate-limited)"


class InputError(ValueError):
    """The user's input can't be run; the message says what to fix."""


class SetupError(RuntimeError):
    """The Actor is set up wrongly (key, model or pricing); only the developer can fix it."""


class Platform(Protocol):
    """Where input comes from and results go: Apify, or a folder on your machine."""

    paid: bool  # True when users pay per event; free models are then refused

    async def get_input(self) -> dict: ...

    def read_dataset(self, dataset_id: str) -> AsyncIterator[dict]: ...

    async def done_items(self) -> list[dict]: ...

    def pricing_problem(self) -> str: ...

    def chargeable(self) -> int | None: ...

    async def push(self, item: dict) -> bool: ...

    async def set_status(self, message: str) -> None: ...

    async def save_summary(self, summary: dict) -> None: ...


# --- Input --------------------------------------------------------------------------------------------------------

def _norm_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


# Field names that hold a company's website in other Actors' datasets (Google Maps, LinkedIn, Apollo exports...),
# best first. Google Maps items also carry `url`, the Maps link, so it comes last.
WEBSITE_KEYS = ("website", "websiteurl", "companywebsite", "organizationwebsite", "domain", "companydomain",
                "primarydomain", "homepage", "url")
NAME_KEYS = ("companyname", "company", "organizationname", "name", "title")
_EXTRA_FACT_KEYS = {
    "Employees": ("employeecount", "estimatednumemployees", "numemployees"),
    "Funding stage": ("latestfundingstage",),
    "Industry": ("categoryname", "category"),
    "Country": ("countrycode",),
}
FACT_KEYS = {label: {_norm_key(n) for n in names} | set(_EXTRA_FACT_KEYS.get(label, ()))
             for label, names in FACT_COLUMNS.items()}

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")
# Listings and social profiles are not the company's own website; researching them would bill for nothing.
DIRECTORY_SITES = ("goo.gl", "g.page", "linkedin.com", "facebook.com", "fb.com", "instagram.com", "twitter.com",
                   "x.com", "youtube.com", "tiktok.com", "yelp.com", "crunchbase.com", "wikipedia.org",
                   "tripadvisor.com", "glassdoor.com")
GOOGLE_RE = re.compile(r"(^|\.)google\.[a-z]{2,3}(\.[a-z]{2})?$")


def _scalars(item: dict) -> dict[str, str]:
    return {_norm_key(k): str(v).strip() for k, v in item.items()
            if isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v).strip()}


def _pick(fields: dict[str, str], keys: tuple[str, ...]) -> str:
    return next((fields[k] for k in keys if k in fields), "")


def item_facts(item: dict) -> dict[str, str]:
    """Firmographics from a dataset item (employee count, industry, country...), passed to the model as list data."""
    fields = _scalars(item)
    facts = {}
    for label, keys in FACT_KEYS.items():
        value = next((v for k, v in fields.items() if k in keys), "")
        if value:
            facts[label] = value[:200]
    return facts


def check_website(value: object) -> tuple[str, str]:
    """(domain, problem): problem is "" when the value is a company's own website."""
    raw = str(value or "").strip()
    domain = normalize_domain(raw) if raw and not re.search(r"\s", raw) else ""
    if not domain or not DOMAIN_RE.match(domain):
        return "", "not a website or domain"
    if GOOGLE_RE.search(domain) or any(domain == d or domain.endswith("." + d) for d in DIRECTORY_SITES):
        return domain, f"{domain} is a listing or social profile, not the company's own website"
    return domain, ""


def _website_entries(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [v for v in re.split(r"[\s,;]+", value) if v]
    if not isinstance(value, list):
        raise InputError("'Company websites' must be a list of domains or URLs.")
    out = []
    for entry in value:
        if isinstance(entry, dict):  # {"url": ...} entries, as in other Actors' start URLs
            entry = _pick(_scalars(entry), WEBSITE_KEYS)
        out.extend(v for v in re.split(r"[\s,;]+", str(entry)) if v)
    return out


def _strings(data: dict, key: str) -> list[str]:
    value = data.get(key)
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise InputError(f"'{key}' must be a list of text lines.")
    return [str(v).strip() for v in value if str(v).strip()]


def _int(data: dict, key: str, default: int | None, low: int, high: int) -> int | None:
    value = data.get(key)
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise InputError(f"'{key}' must be a whole number.") from None
    if not low <= number <= high:
        raise InputError(f"'{key}' must be between {low:,} and {high:,}.")
    return number


def _bool(data: dict, key: str, default: bool) -> bool:
    value = data.get(key)
    if value is None:
        return default
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Job:
    icp: ICP
    writer_cfg: WriterConfig
    companies: list[tuple[str, str, dict[str, str]]]  # (domain, name, list data)
    write_lines: bool = True
    max_pages: int = 4
    rejected: list[dict] = field(default_factory=list)  # input rows that can't be researched, with the reason
    over_limit: int = 0  # valid companies left out by maxCompanies


def parse_input(data: dict, source_items: list[dict] | None = None) -> Job:
    """Turn the Actor input (and the items of a dataset it names) into a target-customer spec and a company list."""
    if not isinstance(data, dict):
        raise InputError("The input must be a JSON object.")
    description = str(data.get("idealCustomer") or "").strip()
    if not description:
        raise InputError("Describe who you sell to in 'Who you sell to' (idealCustomer).")
    low = _int(data, "minEmployees", None, 1, 10_000_000)
    high = _int(data, "maxEmployees", None, 1, 10_000_000)
    if low and high and low > high:
        raise InputError("'Minimum employees' is larger than 'Maximum employees'.")
    icp = ICP(
        name="Ideal customer",
        description=description,
        industries=_strings(data, "industries"),
        employee_range=[low or 1, high or 1_000_000] if (low or high) else [],
        geographies=_strings(data, "countries"),
        stages=_strings(data, "fundingStages"),
        signals=_strings(data, "buyingSignals") if "buyingSignals" in data else list(DEFAULT_SIGNALS),
        exclude=_strings(data, "exclude"),
        min_score=_int(data, "minScore", 6, 0, 10),
        require_size_or_stage=_bool(data, "requireSizeOrStage", True),
    )
    writer_cfg = WriterConfig(offer=str(data.get("offer") or "").strip())

    companies: list[tuple[str, str, dict[str, str]]] = []
    rejected: list[dict] = []
    seen: set[str] = set()

    def add(value: str, name: str = "", facts: dict[str, str] | None = None) -> None:
        domain, problem = check_website(value)
        if problem:
            rejected.append({"website": domain or str(value)[:200], "reason": problem})
        elif domain not in seen:
            seen.add(domain)
            companies.append((domain, name, facts or {}))

    for entry in _website_entries(data.get("websites")):
        add(entry)
    for item in source_items or []:
        if not isinstance(item, dict):
            continue
        fields = _scalars(item)
        website = _pick(fields, WEBSITE_KEYS)
        if website:
            add(website, _pick(fields, NAME_KEYS)[:200], item_facts(item))
        else:
            rejected.append({"website": _pick(fields, NAME_KEYS)[:200] or "(dataset item)",
                             "reason": "no website, domain or url field"})
    if not companies:
        why = f" None of the {len(rejected)} rows given could be used, e.g. {rejected[0]['reason']}." if rejected else ""
        raise InputError("No company websites to score. Add them under 'Company websites' or pick a dataset." + why)
    limit = _int(data, "maxCompanies", 100, 1, MAX_COMPANIES)
    return Job(icp=icp, writer_cfg=writer_cfg, companies=companies[:limit],
               write_lines=_bool(data, "writeOpeningLines", True),
               max_pages=_int(data, "maxPagesPerCompany", 4, 1, 6),
               rejected=rejected, over_limit=max(0, len(companies) - limit))


# --- Model settings -----------------------------------------------------------------------------------------------

def actor_settings(**overrides) -> Settings:
    """leadagent's settings, where LEADACTOR_MODEL and LEADACTOR_FALLBACK_MODELS win over the LEADAGENT_* ones."""
    env = os.environ.get
    fallbacks = env("LEADACTOR_FALLBACK_MODELS")
    settings = Settings.from_env(
        model=env("LEADACTOR_MODEL") or None,
        fallback_models=[m.strip() for m in fallbacks.split(",") if m.strip()] if fallbacks is not None else None)
    settings.app_title = "leadactor"
    settings.web_search = False  # billed per result; not part of the price
    for key, value in overrides.items():
        if value is not None:
            setattr(settings, key, value)
    return settings


def is_free_model(model: str) -> bool:
    return model.endswith(":free") or model.split(":")[0] == "openrouter/free"


def setup_problem(settings: Settings, paid: bool) -> str:
    """Why the Actor must not run with these settings, or "" if it may."""
    if not settings.api_key:
        return "OPENROUTER_API_KEY is not set. Add it as a secret environment variable (see leadactor/README.md)."
    if paid:
        free = [m for m in (settings.model, settings.writer_model, *settings.fallback_models) if m and is_free_model(m)]
        if free:
            return (f"Paid runs can't use free models ({', '.join(free)}): they may log prompts and are capped at "
                    "50-1,000 requests a day. Set LEADACTOR_MODEL to a paid model.")
    return ""


# --- Running ------------------------------------------------------------------------------------------------------

async def score_company(domain: str, name: str, facts: dict[str, str], *, job: Job, settings: Settings,
                        llm, reader) -> CompanyResult:
    result = CompanyResult(domain=domain, input_name=name)
    pages = await reader.read(domain)
    result.pages = [p.url for p in pages]
    if not pages:
        result.status = "unreachable"
        return result
    research = await research_company(llm, job.icp, domain, pages, facts=facts)
    result.research = research
    result.status = "qualified" if research.fits_icp and research.score >= job.icp.min_score else "not_fit"
    if result.status == "qualified" and job.write_lines:
        result.first_line, result.first_line_status = await write_first_line(
            llm, research, job.writer_cfg, model=settings.writer_model or None)
    else:
        result.first_line_status = "skipped"
    return result


def why_now_source(research: CompanyResearch) -> str:
    """The page the why-now came from (finalize() builds why_now from one verified, current signal)."""
    for sig in research.signals:
        text = (sig.headline or sig.evidence).strip()
        if sig.verified and not sig.stale and text and research.why_now.startswith(text[:60]):
            return sig.source_url
    return ""


def to_item(result: CompanyResult, facts: dict[str, str]) -> dict:
    """One dataset row. Only signals whose evidence was found on the cited page are shown."""
    r = result.research
    return {
        "website": result.domain,
        "companyName": r.company_name or result.input_name,
        "qualified": result.status == "qualified",
        "score": r.score,
        "whyNow": r.why_now,
        "whyNowSource": why_now_source(r),
        "openingLine": result.first_line,
        "openingLineStatus": LINE_STATUS.get(result.first_line_status, "not_written"),
        "summary": r.summary,
        "scoreReasons": r.score_reasons,
        "criteria": [{"name": c.name, "status": c.status, "evidence": c.evidence} for c in r.criteria],
        "signals": [{"type": s.type, "headline": s.headline, "evidence": s.evidence, "sourceUrl": s.source_url,
                     "date": s.date, "old": s.stale} for s in r.signals if s.verified],
        "disqualifiers": r.disqualifiers,
        "pagesRead": result.pages,
        "listData": facts,
    }


@dataclass
class Outcome:
    total: int = 0
    already_done: int = 0
    scored: int = 0
    qualified: int = 0
    not_scored: list[dict] = field(default_factory=list)
    not_started: int = 0
    stopped: str = ""

    def message(self) -> str:
        parts = [f"{self.scored} companies scored, {self.qualified} qualified"]
        if self.not_scored:
            parts.append(f"{len(self.not_scored)} not scored and not charged")
        if self.stopped:
            parts.append(f"stopped early: {self.stopped}")
        return "; ".join(parts) + "."


def failure_reason(res: CompanyResult, show_errors: bool) -> str:
    """Why a company wasn't scored. Users on Apify get a plain reason; the raw error can name the model."""
    if res.status == "unreachable":
        return UNREACHABLE
    if show_errors:
        return f"error: {res.error}"
    if res.error.startswith(MODEL_BUSY):
        return "the AI model was busy; try this company again later"
    return "temporary error while scoring; try this company again later"


async def run(job: Job, platform: Platform, *, settings: Settings, llm, reader,
              progress: Callable[[str], None] = print, show_errors: bool = True) -> Outcome:
    outcome = Outcome(total=len(job.companies), not_scored=list(job.rejected))
    done = {str(item.get("website", "")) for item in await platform.done_items()}
    queue = deque(c for c in job.companies if c[0] not in done)
    outcome.already_done = len(job.companies) - len(queue)
    stop = asyncio.Event()
    in_flight = 0
    busy_streak = 0
    finished = 0

    def halt(reason: str) -> None:
        if not stop.is_set():
            outcome.stopped = reason
            stop.set()

    async def process(domain: str, name: str, facts: dict[str, str]) -> None:
        nonlocal busy_streak, finished
        try:
            res = await score_company(domain, name, facts, job=job, settings=settings, llm=llm, reader=reader)
        except DailyLimitError as e:
            queue.appendleft((domain, name, facts))
            halt("the model's daily request limit is reached" + (f" ({str(e)[:120]})" if show_errors else ""))
            return
        except openai.RateLimitError as e:
            res = CompanyResult(domain=domain, input_name=name, status="error",
                                error=f"{MODEL_BUSY}: {str(e)[:200]}")
            busy_streak += 1
            if busy_streak >= BUSY_LIMIT:
                halt(f"the model was rate-limited for {busy_streak} companies in a row; try again later")
        except Exception as e:  # one bad company must not sink the run
            res = CompanyResult(domain=domain, input_name=name, status="error", error=f"{type(e).__name__}: {e}"[:300])
        else:
            busy_streak = 0

        if res.status in SCORED:
            if await platform.push(to_item(res, facts)):
                outcome.scored += 1
                if res.status == "qualified":
                    outcome.qualified += 1
            else:  # the SDK stores nothing it can't charge for; the check before starting should prevent this
                outcome.not_scored.append({"website": domain, "reason": "the run's maximum charge was reached"})
                halt("the run's maximum charge was reached")
        else:
            reason = failure_reason(res, show_errors)
            outcome.not_scored.append({"website": domain, "reason": reason})
        finished += 1
        score = f" {res.research.score}/10" if res.research else ""
        why = f" - {res.research.why_now}" if res.research and res.research.why_now else ""
        progress(f"[{finished + outcome.already_done}/{outcome.total}] {domain}: {res.status}{score}{why}"
                 + (f" - {reason}" if res.status == "error" else ""))
        if finished % 10 == 0:
            await platform.set_status(f"{finished + outcome.already_done} of {outcome.total} companies done")

    async def worker() -> None:
        nonlocal in_flight
        while queue and not stop.is_set():
            left = platform.chargeable()
            if left is not None and left - in_flight <= 0:
                return  # the budget is spoken for; a company still in flight may free it again
            domain, name, facts = queue.popleft()
            in_flight += 1  # holds its place in the budget until its row is charged or it turns out free
            try:
                await process(domain, name, facts)
            finally:
                in_flight -= 1

    await asyncio.gather(*(worker() for _ in range(max(1, settings.concurrency))))
    outcome.not_started = len(queue)
    if queue and not outcome.stopped:
        outcome.stopped = "the run's maximum charge was reached; raise it to score the remaining companies"
    return outcome


async def execute(platform: Platform, settings: Settings, *, llm=None, http: httpx.AsyncClient | None = None,
                  progress: Callable[[str], None] = print, show_errors: bool = True) -> tuple[Outcome, Job, LLM]:
    """Check the setup and the input, then score the companies. Raises SetupError or InputError before any work.

    show_errors=False keeps raw error text (which can name the model) out of what users see.
    """
    problem = setup_problem(settings, platform.paid) or platform.pricing_problem()
    if problem:
        raise SetupError(problem)
    data = await platform.get_input()
    dataset_id = str((data or {}).get("datasetId") or "").strip() if isinstance(data, dict) else ""
    source = None
    if dataset_id:
        try:  # only simple fields are kept, so a dataset of large items (reviews, image lists) fits in memory
            source = [_scalars(item) async for item in platform.read_dataset(dataset_id) if isinstance(item, dict)]
        except Exception as e:
            raise InputError(f"Could not read dataset {dataset_id}: {e}") from e
    job = parse_input(data, source)
    llm = llm or LLM.from_settings(settings)
    extra = f"; {len(job.rejected)} input rows can't be used (free, listed in the summary)" if job.rejected else ""
    extra += f"; {job.over_limit} more left out by 'Max companies'" if job.over_limit else ""
    progress(f"{len(job.companies)} companies to score{extra}")

    async def go(client: httpx.AsyncClient) -> Outcome:
        reader = SiteReader(client, max_pages=job.max_pages, max_chars_per_page=settings.max_chars_per_page)
        return await run(job, platform, settings=settings, llm=llm, reader=reader, progress=progress,
                         show_errors=show_errors)

    if http is not None:
        outcome = await go(http)
    else:
        async with SiteReader.default_client() as client:
            outcome = await go(client)
    return outcome, job, llm


def summary(outcome: Outcome, job: Job) -> dict:
    """The run summary users see next to the dataset (no model names or costs)."""
    return {
        "companiesToScore": outcome.total,
        "scored": outcome.scored,
        "qualified": outcome.qualified,
        "notFit": outcome.scored - outcome.qualified,
        "notScored": len(outcome.not_scored),
        "notScoredList": outcome.not_scored[:MAX_LISTED],
        "notStarted": outcome.not_started,
        "leftOutByMaxCompanies": job.over_limit,
        "alreadyInDataset": outcome.already_done,
        "stoppedEarly": outcome.stopped,
        "billing": "One charge per dataset row: a company whose website was read and scored. "
                   "Companies in notScoredList were not charged.",
    }
