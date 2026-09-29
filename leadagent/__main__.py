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
    fallbacks = [m.strip() for m in args.fallback.split(",") if m.strip()] if args.fallback else None
    s = Settings.from_env(model=args.model, fallback_models=fallbacks, rpm=args.rpm, concurrency=args.concurrency,
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


async def _models(args) -> None:
    """List models from OpenRouter's public catalogue, flagging which promise JSON output."""
    import httpx

    base = Settings.from_env().base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.get(f"{base}/models")
        r.raise_for_status()
        models = r.json().get("data", [])

    def is_free(m) -> bool:
        p = m.get("pricing") or {}
        return str(p.get("prompt", "1")) in ("0", "0.0") and str(p.get("completion", "1")) in ("0", "0.0")

    def writes_text(m) -> bool:  # skip music/image generators such as Lyria
        out = (m.get("architecture") or {}).get("output_modalities")
        return out is None or "text" in out

    rows = [m for m in models if writes_text(m) and (is_free(m) or not args.free)]
    rows.sort(key=lambda m: ("response_format" not in (m.get("supported_parameters") or []), m.get("id", "")))
    print(f"{'model id':60} {'context':>9}  json")
    for m in rows:
        params = m.get("supported_parameters")
        json_ok = "?" if params is None else ("yes" if {"response_format", "structured_outputs"} & set(params) else "no")
        print(f"{m.get('id', ''):60} {m.get('context_length') or '':>9}  {json_ok}")
    print(f"\n{len(rows)} models. Pin one with LEADAGENT_MODEL=<id> in .env; prefer json=yes.")


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
        backups = f" (backups: {', '.join(s.fallback_models)})" if s.fallback_models else ""
        print(f"{len(companies)} companies; model {s.model}{backups}; {s.rpm:g} requests/min; "
              f"web search {'on' if s.web_search else 'off'}")
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
        sp.add_argument("--fallback", help="comma-separated backup model ids, tried when the main one is busy")
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
    m = sub.add_parser("models", help="list OpenRouter models (no key needed)")
    m.add_argument("--free", action="store_true", help="only free models")

    args = p.parse_args(argv)
    load_dotenv()  # keys stay in the environment; .env is optional and git-ignored
    handler = {"run": _run, "check": _check, "models": _models}[args.cmd]
    asyncio.run(handler(args))


if __name__ == "__main__":
    main()
