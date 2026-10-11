"""Command line: `python -m upworkbid sample | jobs | run | score`."""

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
from agentkit.settings import Settings, check_key, plural, require

from .jobs import apply_rules, client_score, read_jobs
from .output import write_outputs
from .pipeline import CACHE_VERSION, assess_all
from .profile import load_profile


def _today(args) -> date:
    if not getattr(args, "today", None):
        return date.today()
    d = parse_date(args.today)
    if d is None:
        sys.exit(f"Could not read --today '{args.today}'; use YYYY-MM-DD.")
    return d


def _load(args):
    require(args.profile, args.jobs)
    try:
        p = load_profile(args.profile)
    except ValueError as e:
        sys.exit(str(e))
    jobs, skipped = read_jobs(args.jobs)
    print(f"Jobs: {plural(len(jobs), 'post')} in {args.jobs}")
    for x in skipped:
        print(f"  skipped {x}")
    if not jobs:
        sys.exit("No job posts found. Save each Upwork job page's text as a .txt file in the folder.")
    return p, jobs


def _jobs_cmd(args) -> None:
    p, jobs = _load(args)
    today = _today(args)
    left = 0
    for j in jobs:
        apply_rules(j, p, today)
        score, _ = client_score(j, today)
        left += not j.out
        state = "skip: " + "; ".join(j.out) if j.out else "to assess"
        print(f"  {j.file}: {j.budget_text}, client {score}/100, {j.proposals or '?'} proposals: {state}")
    print(f"{len(jobs)} posts; {left} left to assess = {plural(left, 'model request')}.")


async def _run(args) -> None:
    p, jobs = _load(args)
    today = _today(args)
    s = Settings.from_env("UPWORKBID", "upworkbid")
    if args.model:
        s.model = args.model
    check_key(s)  # job posts are public, so free models are fine here
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Model {s.model}; assessing for {p.name} (floor ${p.floor_after_fees:,.0f}/h after Upwork's "
          f"{p.fee:.0%} fee)")
    llm = LLM.from_settings(s)
    runner = Runner(llm, out / "cache", CACHE_VERSION, hint="set UPWORKBID_FALLBACK_MODELS")
    decisions = await assess_all(jobs, p, runner, today)
    stats = write_outputs(out, decisions)
    (out / "run_summary.json").write_text(json.dumps({**stats, "llm_requests": llm.usage.requests,
                                                     "cost_usd": round(llm.usage.cost_usd, 6),
                                                     "stopped_reason": runner.stopped_reason}, indent=2))
    print(f"\nDone: {stats['bids']} to bid on, {stats['maybe']} maybe, {stats['skips']} skipped; "
          f"{plural(llm.usage.requests, 'model request')}; ${llm.usage.cost_usd:.4f}")
    if runner.stopped_reason:
        print("Stopped early: " + runner.stopped_reason)
    print(f"Bidding on all {stats['bids']} costs {stats['connects']} Connects (about ${stats['connects_cost']:.2f}).")
    print(f"Ranked list: {out / 'jobs.csv'}; drafts to paste into Upwork: {out / 'proposals'}/ (read each one, then "
          "send it yourself)")


def _sample(args) -> None:
    from .sample import make_sample

    out = Path(args.out)
    truth = make_sample(out)
    print(f"Wrote {out / 'profile.toml'}, {plural(len(truth['jobs']), 'job post')} in {out / 'jobs'} and the answer key "
          f"{out / 'truth.json'}. Use --today {truth['today']} with this example.")


def _score(args) -> None:
    from .sample import format_score, score

    require(args.run, args.truth)
    results = json.loads((Path(args.run) / "jobs.json").read_text())
    print(format_score(score(results, json.loads(Path(args.truth).read_text()))))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="upworkbid", description="Which Upwork jobs to bid on, with checked proposals.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sm = sub.add_parser("sample", help="write the synthetic profile and job posts (fictional) with the answer key")
    sm.add_argument("--out", default="examples/upworkbid", help="folder (default examples/upworkbid)")

    def base(sp):
        sp.add_argument("--profile", required=True, help="your profile (TOML): rate floor, skills, portfolio, hours")
        sp.add_argument("--jobs", required=True, help="folder of job posts saved as .txt files")
        sp.add_argument("--today", help="treat this date as today, YYYY-MM-DD")

    jb = sub.add_parser("jobs", help="the money and client checks only (no model calls)")
    base(jb)

    r = sub.add_parser("run", help="decide bid / maybe / skip and draft the proposals")
    base(r)
    r.add_argument("--out", default="out/upworkbid", help="output folder (default out/upworkbid)")
    r.add_argument("--model", help="model id (default UPWORKBID_MODEL, then LEADAGENT_MODEL)")

    sc = sub.add_parser("score", help="compare a run on the synthetic posts with the answer key")
    sc.add_argument("--run", default="out/upworkbid", help="the run's output folder (default out/upworkbid)")
    sc.add_argument("--truth", default="examples/upworkbid/truth.json", help="answer key from `sample`")

    args = p.parse_args(argv)
    load_dotenv()
    if args.cmd == "sample":
        _sample(args)
    elif args.cmd == "jobs":
        _jobs_cmd(args)
    elif args.cmd == "run":
        asyncio.run(_run(args))
    else:
        _score(args)


if __name__ == "__main__":
    main()
