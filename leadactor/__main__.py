"""Command line: `python -m leadactor run ...` on your machine, `python -m leadactor apify` on the Apify platform."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from agentkit.env import load_dotenv

from .actor import MAX_MODEL_COST_SHARE, InputError, SetupError, actor_settings, execute, summary
from .platforms import LocalPlatform, run_on_apify


def cost_report(scored: int, usage, model: str, price: float | None) -> dict:
    """What a run cost you in model fees, next to what users would pay (local runs only; users never see it)."""
    per_company = usage.cost_usd / scored if scored else None
    report = {
        "model": model,
        "modelsUsed": usage.models,
        "modelRequests": usage.requests,
        "modelCostUsd": round(usage.cost_usd, 4),
        "modelCostPerScoredCompanyUsd": round(per_company, 5) if per_company is not None else None,
    }
    if price:
        report["simulatedPriceUsd"] = price
        report["simulatedChargeUsd"] = round(scored * price, 2)
        report["modelCostShareOfPrice"] = round(per_company / price, 3) if per_company is not None else None
    return report


def cost_line(report: dict) -> str:
    per = report["modelCostPerScoredCompanyUsd"]
    if per is None:
        return "No company was scored, so there is no cost per company yet."
    if per == 0:
        return ("Model cost $0 (free model). Before setting a price, measure a paid model: "
                "--model <paid model id> --price 0.03")
    line = f"Model cost per scored company: ${per:.4f}"
    share = report.get("modelCostShareOfPrice")
    if share is not None:
        verdict = "fine" if share <= MAX_MODEL_COST_SHARE else "too high: raise the price or pick a cheaper model"
        line += (f", {share:.0%} of the ${report['simulatedPriceUsd']:g} price "
                 f"(keep it under {MAX_MODEL_COST_SHARE:.0%}: {verdict})")
    return line


async def _run(args) -> None:
    path = Path(args.input)
    if not path.is_file():
        sys.exit(f"File not found: {args.input}\n(current folder: {Path.cwd()})")
    data = json.loads(path.read_text(encoding="utf-8"))
    if args.limit:
        data["maxCompanies"] = args.limit
    fallbacks = [m.strip() for m in args.fallback.split(",") if m.strip()] if args.fallback else None
    settings = actor_settings(model=args.model, fallback_models=fallbacks, rpm=args.rpm, concurrency=args.concurrency)
    platform = LocalPlatform(data, Path(args.out), price=args.price, max_charge=args.max_charge, resume=args.resume)
    print(f"model {settings.model}; {settings.rpm:g} requests/min"
          + (f"; simulated price ${args.price:g} per company" if args.price else "")
          + (f", maximum charge ${args.max_charge:g}" if args.max_charge else ""))
    try:
        outcome, job, llm = await execute(platform, settings)
    except (InputError, SetupError) as e:
        sys.exit(str(e))
    report = cost_report(outcome.scored, llm.usage, settings.model, args.price)
    await platform.save_summary({**summary(outcome, job), "yourCosts": report})
    print("\n" + outcome.message())
    print(cost_line(report))
    print(f"Files in {args.out}/: results.csv, dataset.jsonl (what Apify users get), run_summary.json")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="leadactor", description="The lead agent as a pay-per-event Apify Actor.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the Actor on your machine with an input JSON file")
    r.add_argument("--input", required=True, help="Actor input as JSON (see examples/leadactor/input.example.json)")
    r.add_argument("--out", default="out/leadactor", help="output folder (default out/leadactor)")
    r.add_argument("--limit", type=int, help="score only the first N companies")
    r.add_argument("--price", type=float, help="simulated price per scored company in USD, e.g. 0.03")
    r.add_argument("--max-charge", type=float, help="simulated maximum charge per run in USD, e.g. 0.50")
    r.add_argument("--resume", action="store_true", help="keep dataset.jsonl from the last run and skip its companies")
    r.add_argument("--model", help="OpenRouter model id (default: LEADACTOR_MODEL, then LEADAGENT_MODEL)")
    r.add_argument("--fallback", help="comma-separated backup model ids")
    r.add_argument("--rpm", type=float, help="max model requests per minute (default 18)")
    r.add_argument("--concurrency", type=int, help="companies processed in parallel (default 4)")
    sub.add_parser("apify", help="entry point on the Apify platform (needs the apify package)")

    args = p.parse_args(argv)
    load_dotenv()  # keys stay in the environment; .env is optional and git-ignored
    asyncio.run(run_on_apify() if args.cmd == "apify" else _run(args))


if __name__ == "__main__":
    main()
