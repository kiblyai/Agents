"""Command line: `python -m txcoord sample | files | run | score`."""

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

from .config import load_tc
from .output import write_all
from .pipeline import CACHE_VERSION, read_all, read_folder, transaction_folders


def _today(args) -> date:
    if not args.today:
        return date.today()
    d = parse_date(args.today)
    if d is None:
        sys.exit(f"Could not read --today '{args.today}'; use YYYY-MM-DD.")
    return d


def _cfg(args):
    require(args.tc)
    try:
        return load_tc(args.tc)  # defaults when no --tc
    except ValueError as e:
        sys.exit(str(e))


def _files_cmd(args) -> None:
    require(args.files)
    folders = transaction_folders(args.files)
    for f in folders:
        docs, skipped = read_folder(f)
        print(f"  {f.name}: {', '.join(n for n, _ in docs) or 'no readable documents'}"
              + (f"; skipped {', '.join(skipped)}" if skipped else ""))
    print(f"{plural(len(folders), 'transaction')} = {plural(len(folders), 'model request')} "
          "(free models allow 50 a day, 1,000 after $10 of credit).")


async def _run(args) -> None:
    require(args.files)
    cfg, today = _cfg(args), _today(args)
    folders = transaction_folders(args.files)
    s = Settings.from_env("TXCOORD", "txcoord")
    if args.model:
        s.model = args.model
    check_key(s)
    if is_free_model(s.model):
        print(free_model_warning("TXCOORD", "a real client's contract (names, addresses and loan details)"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Model {s.model}; reading {plural(len(folders), 'transaction')} as of {today.isoformat()}")
    llm = LLM.from_settings(s)
    runner = Runner(llm, out / "cache", CACHE_VERSION, hint="set TXCOORD_FALLBACK_MODELS")
    ts = await read_all(folders, cfg, runner, today)
    stats = write_all(out, ts, cfg, today)
    ready = sum(1 for t in ts if t.ready)
    summary = {"transactions": len(ts), "ready": ready, "to_fix": len(ts) - ready, "deadlines": stats["deadlines"],
               "review_items": stats["review_items"], "stopped_reason": runner.stopped_reason,
               "llm_requests": llm.usage.requests, "llm_repairs": llm.usage.repairs,
               "prompt_tokens": llm.usage.prompt_tokens, "completion_tokens": llm.usage.completion_tokens,
               "cost_usd": round(llm.usage.cost_usd, 6), "models_used": llm.usage.models, "from_cache": runner.from_cache}
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nDone: {ready} of {plural(len(ts), 'file')} ready, {len(ts) - ready} to fix; "
          f"{stats['deadlines']} deadlines; {plural(llm.usage.requests, 'model request')}; ${llm.usage.cost_usd:.4f}")
    if runner.stopped_reason:
        print("Stopped early: " + runner.stopped_reason)
    print(f"Check first: {out / 'review.csv'} ({plural(stats['review_items'], 'item')})")
    print(f"Every deadline across files: {out / 'all_deadlines.csv'}; per file: timeline.csv, calendar.ics, "
          "checklist.md and emails/ in its own folder")


def _sample(args) -> None:
    from .sample import make_sample

    out = Path(args.out)
    truth = make_sample(out)
    print(f"Wrote {plural(len(truth['transactions']), 'transaction')} to {out / 'transactions'}, {out / 'tc.toml'} and "
          f"the answer key {out / 'truth.json'}. Use --today {truth['today']} with this example.")


def _score(args) -> None:
    from .sample import format_score, score

    require(args.run, args.truth)
    results = json.loads((Path(args.run) / "results.json").read_text())
    print(format_score(score(results, json.loads(Path(args.truth).read_text()))))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="txcoord", description="Purchase-contract deadlines, calendar and checklist.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sm = sub.add_parser("sample", help="write the synthetic transactions (fictional) with the answer key")
    sm.add_argument("--out", default="examples/txcoord", help="folder (default examples/txcoord)")

    fl = sub.add_parser("files", help="list the transactions and documents found (no model calls)")
    fl.add_argument("--files", required=True, help="folder with one subfolder per transaction")

    r = sub.add_parser("run", help="read each contract and work out the deadlines")
    r.add_argument("--files", required=True, help="folder with one subfolder per transaction (contract + addenda)")
    r.add_argument("--tc", help="coordinator settings (TOML); defaults otherwise")
    r.add_argument("--today", help="treat this date as today, YYYY-MM-DD (default today)")
    r.add_argument("--out", default="out/txcoord", help="output folder (default out/txcoord)")
    r.add_argument("--model", help="model id (default TXCOORD_MODEL, then LEADAGENT_MODEL)")

    sc = sub.add_parser("score", help="compare a run on the synthetic transactions with the answer key")
    sc.add_argument("--run", default="out/txcoord", help="the run's output folder (default out/txcoord)")
    sc.add_argument("--truth", default="examples/txcoord/truth.json", help="answer key from `sample`")

    args = p.parse_args(argv)
    load_dotenv()
    if args.cmd == "sample":
        _sample(args)
    elif args.cmd == "files":
        _files_cmd(args)
    elif args.cmd == "run":
        asyncio.run(_run(args))
    else:
        _score(args)


if __name__ == "__main__":
    main()
