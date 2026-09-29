"""Command line: `python -m leadagent run ...` and `python -m leadagent check`."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pydantic import BaseModel

from .config import Settings, load_dotenv, load_spec
from .contacts import SyntaxOnlyVerifier, load_companies, load_contacts
from .fetch import SiteReader
from .llm import LLM
from .output import COMPANY_COLUMNS, LEAD_COLUMNS, company_rows, lead_rows, qa_sample, write_csv
from .pipeline import dump_json, run, summary


class _Ping(BaseModel):
    ok: bool


def _settings(args) -> Settings:
    s = Settings.from_env(model=args.model, rpm=args.rpm, concurrency=args.concurrency,
                          web_search=True if args.web_search else None, max_pages=args.max_pages)
    if not s.api_key:
        sys.exit("OPENROUTER_API_KEY is not set. Put it in your environment or a local .env file (see README.md).")
    return s


async def _check(args) -> None:
    s = _settings(args)
    llm = LLM.from_settings(s)
    reply = await llm.complete_json(system='Reply with the JSON object {"ok": true} and nothing else.',
                                    user="ping", schema=_Ping, max_tokens=20)
    print(f"model {s.model} replied ok={reply.ok}; tokens in/out {llm.usage.prompt_tokens}/{llm.usage.completion_tokens}")


async def _run(args) -> None:
    s = _settings(args)
    icp, writer_cfg = load_spec(args.icp)
    companies = load_companies(args.companies)
    contacts = load_contacts(args.contacts or args.companies)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    llm = LLM.from_settings(s)
    async with SiteReader.default_client() as http:
        reader = SiteReader(http, max_pages=s.max_pages, max_chars_per_page=s.max_chars_per_page)
        print(f"{len(companies)} companies; model {s.model}; {s.rpm:g} requests/min; web search {'on' if s.web_search else 'off'}")
        report = await run(companies, icp=icp, writer_cfg=writer_cfg, settings=s, llm=llm, reader=reader,
                           cache_dir=out / "cache", limit=args.limit)
    leads = await lead_rows(report.results, contacts, icp.target_titles, s.contacts_per_company, SyntaxOnlyVerifier())
    write_csv(out / "leads.csv", leads, LEAD_COLUMNS)
    write_csv(out / "companies.csv", company_rows(report.results), COMPANY_COLUMNS)
    write_csv(out / "qa_sample.csv", qa_sample(leads), LEAD_COLUMNS)
    (out / "companies.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in report.results))
    stats = summary(report, llm.usage, len(companies))
    dump_json(out / "run_summary.json", stats)
    print(f"\nDone: {stats['status_counts']}; {len(leads)} lead rows; {stats['llm_requests']} model requests; "
          f"${stats['cost_usd']:.4f}")
    if report.stopped_reason:
        print("Stopped early: " + report.stopped_reason)
    print(f"Files in {out}/: leads.csv, companies.csv, qa_sample.csv, companies.jsonl, run_summary.json")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="leadagent", description="Research companies and produce scored, cited leads.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--model", help="OpenRouter model id (default: LEADAGENT_MODEL or openrouter/free)")
        sp.add_argument("--rpm", type=float, help="max model requests per minute (default 18)")
        sp.add_argument("--concurrency", type=int, help="companies processed in parallel (default 4)")
        sp.add_argument("--web-search", action="store_true", help="add OpenRouter web search (paid per result)")
        sp.add_argument("--max-pages", type=int, help="pages read per company (default 4)")

    r = sub.add_parser("run", help="research a list of companies")
    r.add_argument("--icp", required=True, help="target-customer spec, TOML (see examples/icp.example.toml)")
    r.add_argument("--companies", required=True, help="CSV with a domain/website column")
    r.add_argument("--contacts", help="CSV of people (domain or email, name, title); default: read from --companies")
    r.add_argument("--out", default="out", help="output folder (default ./out)")
    r.add_argument("--limit", type=int, help="only the first N companies (use 25 for a free sample)")
    common(r)
    c = sub.add_parser("check", help="test the API key and model with one tiny request")
    common(c)

    args = p.parse_args(argv)
    load_dotenv()  # keys stay in the environment; .env is optional and git-ignored
    asyncio.run(_run(args) if args.cmd == "run" else _check(args))


if __name__ == "__main__":
    main()
