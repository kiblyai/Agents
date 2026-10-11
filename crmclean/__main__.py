"""Command line: `python -m crmclean sample | check | run | apply | score`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from agentkit.checks import parse_date
from agentkit.env import load_dotenv
from agentkit.llm import LLM
from agentkit.runner import Runner
from agentkit.settings import Settings, check_key, free_model_warning, is_free_model, plural, require

from .config import load_crm
from .output import approved, updates, write_imports, write_outputs
from .pipeline import CACHE_VERSION, clean, enrich
from .records import read_export


def _today(args) -> date:
    if not getattr(args, "today", None):
        return date.today()
    d = parse_date(args.today)
    if d is None:
        sys.exit(f"Could not read --today '{args.today}'; use YYYY-MM-DD.")
    return d


def _load(args):
    require(args.contacts, args.companies, args.crm)
    try:
        cfg = load_crm(args.crm)
        contacts, c_unused = read_export(args.contacts, "contact") if args.contacts else ([], [])
        companies, k_unused = read_export(args.companies, "company") if args.companies else ([], [])
    except ValueError as e:
        sys.exit(str(e))
    print(f"Export: {plural(len(contacts), 'contact')}, {plural(len(companies), 'company', 'companies')}")
    return cfg, contacts, companies


def _report(res, out: Path, stats: dict) -> None:
    rule = sum(1 for c in res.changes if c.by == "rule")
    print(f"\nProposed: {rule} fixes by rule, {len(res.changes) - rule} values from websites, "
          f"{plural(len(res.merges), 'merge')}; {plural(stats['review'], 'record')} to look at")
    print(f"Approve in {out / 'changes.csv'} and {out / 'merges.csv'} (column 'approve': fixes by rule are "
          "pre-approved; website values and merges need your yes), or read it all in cleanup.xlsx")
    print(f"Look at: {out / 'review.csv'}. Then run `python -m crmclean apply --run {out}`")


def _check(args) -> None:
    cfg, contacts, companies = _load(args)
    res = clean(contacts, companies, cfg, _today(args))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stats = write_outputs(out, res)
    _report(res, out, stats)


async def _run(args) -> None:
    cfg, contacts, companies = _load(args)
    res = clean(contacts, companies, cfg, _today(args))
    s = Settings.from_env("CRMCLEAN", "crmclean")
    if args.model:
        s.model = args.model
    check_key(s)
    if is_free_model(s.model):
        print(free_model_warning("CRMCLEAN", "a client's CRM export (it holds people's personal data)"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    llm = LLM.from_settings(s)
    runner = Runner(llm, out / "cache", CACHE_VERSION, hint="set CRMCLEAN_FALLBACK_MODELS")
    reader = http = None
    if args.fetch:
        from leadagent.fetch import SiteReader
        http = SiteReader.default_client()
        reader = SiteReader(http, max_pages=2)
    sites = Path(args.sites) if args.sites else None
    print(f"Model {s.model}; filling in companies from " + ("saved pages in " + str(sites) if sites else "")
          + (" and " if sites and reader else "") + ("their live websites" if reader else ""))
    try:
        await enrich(res, companies, cfg, runner, sites, reader, args.limit)
    finally:
        if http is not None:
            await http.aclose()
    stats = write_outputs(out, res)
    summary = {**stats, "enriched_companies": res.enriched, "no_page": res.no_page,
               "llm_requests": llm.usage.requests, "cost_usd": round(llm.usage.cost_usd, 6),
               "prompt_tokens": llm.usage.prompt_tokens, "completion_tokens": llm.usage.completion_tokens,
               "models_used": llm.usage.models, "from_cache": runner.from_cache, "stopped_reason": runner.stopped_reason}
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2))
    _report(res, out, stats)
    print(f"{plural(llm.usage.requests, 'model request')}; ${llm.usage.cost_usd:.4f}")
    if runner.stopped_reason:
        print("Stopped early: " + runner.stopped_reason)


def _apply(args) -> None:
    from .hubspot import HubSpot, HubSpotError, apply, token_from_env

    run = Path(args.run)
    require(run / "changes.csv", run / "merges.csv")
    changes, merges = approved(run)
    files = write_imports(run, changes)
    props, links = updates(changes)
    print(f"Approved: {plural(len(changes), 'change')}, {plural(len(merges), 'merge')}")
    for f in files:
        print(f"  import file: {f}")
    if not args.yes:
        print("Dry run: nothing was sent. Import the files above in HubSpot (Import > Update existing records by "
              "Record ID), or add --yes to send the changes, links and merges through the API.")
        return
    token = token_from_env()
    if not token:
        sys.exit("Set HUBSPOT_ACCESS_TOKEN (a private app token with crm.objects.contacts.write and "
                 "crm.objects.companies.write) in your environment or .env.")
    try:
        done = apply(HubSpot(token), props, links, merges)
    except HubSpotError as e:
        sys.exit(str(e))
    print(f"Sent: {done['merges']} merges, {done['contacts']} contacts and {done['companies']} companies updated, "
          f"{done['associations']} contacts linked to their company")


def _sample(args) -> None:
    from .sample import make_sample

    out = Path(args.out)
    truth = make_sample(out)
    print(f"Wrote {out / 'companies.csv'}, {out / 'contacts.csv'}, {len(truth['enrich'])} saved homepages in "
          f"{out / 'sites'}, crm.toml and the answer key {out / 'truth.json'}. Use --today {truth['today']}.")


def _score(args) -> None:
    from .sample import format_score, score

    require(args.run, args.truth)
    results = json.loads((Path(args.run) / "results.json").read_text())
    print(format_score(score(results, json.loads(Path(args.truth).read_text()))))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="crmclean", description="HubSpot cleanup: duplicates, formats and enrichment.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sm = sub.add_parser("sample", help="write the synthetic portal (fictional companies) with its answer key")
    sm.add_argument("--out", default="examples/crmclean", help="folder (default examples/crmclean)")

    def base(sp):
        sp.add_argument("--contacts", help="HubSpot contacts export (CSV or Excel)")
        sp.add_argument("--companies", help="HubSpot companies export (CSV or Excel)")
        sp.add_argument("--crm", help="cleanup settings (TOML); defaults otherwise")
        sp.add_argument("--today", help="treat this date as today, YYYY-MM-DD (for stale records)")
        sp.add_argument("--out", default="out/crmclean", help="output folder (default out/crmclean)")

    ch = sub.add_parser("check", help="the rules only: duplicates, formats, links, stale records (no model calls)")
    base(ch)

    r = sub.add_parser("run", help="the rules plus missing company details from their websites")
    base(r)
    r.add_argument("--sites", help="folder of saved homepages named <domain>.html (instead of, or before, --fetch)")
    r.add_argument("--fetch", action="store_true", help="read companies' live websites")
    r.add_argument("--model", help="model id (default CRMCLEAN_MODEL, then LEADAGENT_MODEL)")
    r.add_argument("--limit", type=int, help="only fill in the first N companies (for a quick test)")

    ap = sub.add_parser("apply", help="turn approved rows into HubSpot import files, or send them (--yes)")
    ap.add_argument("--run", default="out/crmclean", help="the run's output folder (default out/crmclean)")
    ap.add_argument("--yes", action="store_true", help="send the approved changes, links and merges through the API")

    sc = sub.add_parser("score", help="compare a run on the synthetic portal with the answer key")
    sc.add_argument("--run", default="out/crmclean", help="the run's output folder (default out/crmclean)")
    sc.add_argument("--truth", default="examples/crmclean/truth.json", help="answer key from `sample`")

    args = p.parse_args(argv)
    load_dotenv()
    if args.cmd == "sample":
        _sample(args)
    elif args.cmd == "check":
        _check(args)
    elif args.cmd == "run":
        asyncio.run(_run(args))
    elif args.cmd == "apply":
        _apply(args)
    else:
        _score(args)


if __name__ == "__main__":
    main()
