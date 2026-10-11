"""Command line: `python -m govbids sample | notices | fetch | match | shred | score`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from agentkit.checks import parse_date
from agentkit.docs import document_files, read_document
from agentkit.env import load_dotenv
from agentkit.llm import LLM
from agentkit.runner import Runner
from agentkit.settings import Settings, check_key, plural, require

from .notices import apply_rules, read_notices
from .output import write_matches, write_shred
from .pipeline import BATCH, CACHE_VERSION, match_all, shred
from .profile import load_profile


def _today(args) -> date:
    if not getattr(args, "today", None):
        return date.today()
    d = parse_date(args.today)
    if d is None:
        sys.exit(f"Could not read --today '{args.today}'; use YYYY-MM-DD.")
    return d


def _profile(path):
    require(path)
    try:
        return load_profile(path)
    except ValueError as e:
        sys.exit(str(e))


def _notices(path):
    require(path)
    try:
        return read_notices(path)
    except ValueError as e:
        sys.exit(str(e))


def _llm(args):
    s = Settings.from_env("GOVBIDS", "govbids")
    if args.model:
        s.model = args.model
    check_key(s)  # SAM.gov notices and RFPs are public, so free models are fine here
    return s, LLM.from_settings(s)


def _notices_cmd(args) -> None:
    p, today = _profile(args.company), _today(args)
    notices = _notices(args.notices)
    left = []
    for n in notices:
        apply_rules(n, p, today)
        if not n.out:
            left.append(n)
    for n in notices:
        state = "filtered out: " + "; ".join(n.out) if n.out else "to score"
        print(f"  {n.id} {n.title[:60]}: {state}" + (f" ({'; '.join(n.notes)})" if n.notes else ""))
    requests = -(-len(left) // BATCH)
    print(f"{len(notices)} notices; {len(left)} left to score = {plural(requests, 'model request')}.")


def _fetch(args) -> None:
    from .fetch import fetch_notices, key_from_env, save

    p = _profile(args.company)
    key = key_from_env()
    if not key:
        sys.exit("Set SAM_API_KEY (free from your SAM.gov account: Account Details > API Key) in your environment or .env.")
    today = _today(args)
    rows = fetch_notices(key, today - timedelta(days=args.days), today, p.naics)
    save(rows, args.out)
    print(f"Saved {plural(len(rows), 'notice')} posted in the last {args.days} days for NAICS {', '.join(p.naics)} to "
          f"{args.out}")


async def _match(args) -> None:
    p, today = _profile(args.company), _today(args)
    notices = _notices(args.notices)
    s, llm = _llm(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Model {s.model}; checking {plural(len(notices), 'notice')} for {p.name}")
    runner = Runner(llm, out / "cache", CACHE_VERSION, hint="set GOVBIDS_FALLBACK_MODELS")
    matches = await match_all(notices, p, runner, today)
    rows = write_matches(out, matches, p.name, today)
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("bid", "respond", "pass", "not scored",
                                                                     "filtered out")}
    (out / "matches_summary.json").write_text(json.dumps({**counts, "llm_requests": llm.usage.requests,
                                                         "cost_usd": round(llm.usage.cost_usd, 6),
                                                         "stopped_reason": runner.stopped_reason}, indent=2))
    print(f"\nDone: {counts['bid']} to bid, {counts['respond']} to respond to, {counts['pass']} to pass, "
          f"{counts['filtered out']} filtered out; {plural(llm.usage.requests, 'model request')}; "
          f"${llm.usage.cost_usd:.4f}")
    if runner.stopped_reason:
        print("Stopped early: " + runner.stopped_reason)
    print(f"Read first: {out / 'digest.md'}; everything in {out / 'matches.csv'}")


async def _shred(args) -> None:
    require(args.rfp)
    path = Path(args.rfp)
    files = [path] if path.is_file() else document_files(path)[0]
    docs = []
    for f in files:
        try:
            docs += read_document(f)
        except ValueError as e:
            print(f"  skipped {e}")
    if not docs or not any(t.strip() for _, t in docs):
        sys.exit("No readable text in the RFP (a scanned PDF needs text recognition first, e.g. ocrmypdf).")
    s, llm = _llm(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Model {s.model}; shredding {', '.join(f.name for f in files)}")
    runner = Runner(llm, out / "cache", CACHE_VERSION, hint="set GOVBIDS_FALLBACK_MODELS")
    reqs, facts, checks = await shred(docs, runner)
    stats = write_shred(out, reqs, facts, checks)
    print(f"\nDone: {plural(stats['requirements'], 'requirement')} ({stats['offeror']} for the offeror), "
          f"{sum(1 for f in facts if f['value'])} key facts; {plural(llm.usage.requests, 'model request')}; "
          f"${llm.usage.cost_usd:.4f}")
    if runner.stopped_reason:
        print("Stopped early: " + runner.stopped_reason)
    print(f"Compliance matrix: {out / 'compliance_matrix.xlsx'}; check {out / 'review_shred.csv'} "
          f"({plural(stats['review'], 'item')})")


def _sample(args) -> None:
    from .sample import make_sample

    out = Path(args.out)
    truth = make_sample(out)
    print(f"Wrote {out / 'company.toml'}, {out / 'notices.csv'} ({len(truth['notices'])} notices), {out / 'rfp'} and the "
          f"answer key {out / 'truth.json'}. Use --today {truth['today']} with this example.")


def _score(args) -> None:
    from .sample import format_score, score

    require(args.run, args.truth)
    run = Path(args.run)

    def load(name):
        p = run / name
        return json.loads(p.read_text()) if p.exists() else None

    print(format_score(score(load("matches.json"), load("shred.json"), json.loads(Path(args.truth).read_text()))))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="govbids", description="Federal bid matching and RFP compliance matrices.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sm = sub.add_parser("sample", help="write the synthetic contractor, notices and RFP (fictional) with the answer key")
    sm.add_argument("--out", default="examples/govbids", help="folder (default examples/govbids)")

    nt = sub.add_parser("notices", help="apply the rules to the notices (no model calls)")
    nt.add_argument("--company", required=True, help="company profile (TOML)")
    nt.add_argument("--notices", required=True, help="notices CSV (SAM.gov export or `fetch` output)")
    nt.add_argument("--today", help="treat this date as today, YYYY-MM-DD")

    fe = sub.add_parser("fetch", help="download recent notices for your NAICS codes from the SAM.gov API")
    fe.add_argument("--company", required=True, help="company profile (TOML), for its NAICS codes")
    fe.add_argument("--days", type=int, default=7, help="notices posted in the last N days (default 7)")
    fe.add_argument("--today", help="treat this date as today, YYYY-MM-DD")
    fe.add_argument("--out", default="out/govbids/notices.csv", help="where to save (default out/govbids/notices.csv)")

    m = sub.add_parser("match", help="filter and score the notices; writes matches.csv and digest.md")
    m.add_argument("--company", required=True, help="company profile (TOML)")
    m.add_argument("--notices", required=True, help="notices CSV (SAM.gov export or `fetch` output)")
    m.add_argument("--today", help="treat this date as today, YYYY-MM-DD")
    m.add_argument("--out", default="out/govbids", help="output folder (default out/govbids)")
    m.add_argument("--model", help="model id (default GOVBIDS_MODEL, then LEADAGENT_MODEL)")

    sh = sub.add_parser("shred", help="turn an RFP into a compliance matrix and key facts")
    sh.add_argument("--rfp", required=True, help="the RFP: a PDF/Word file or a folder of them")
    sh.add_argument("--out", default="out/govbids", help="output folder (default out/govbids)")
    sh.add_argument("--model", help="model id (default GOVBIDS_MODEL, then LEADAGENT_MODEL)")

    sc = sub.add_parser("score", help="compare a run on the synthetic example with the answer key")
    sc.add_argument("--run", default="out/govbids", help="the run's output folder (default out/govbids)")
    sc.add_argument("--truth", default="examples/govbids/truth.json", help="answer key from `sample`")

    args = p.parse_args(argv)
    load_dotenv()
    if args.cmd == "sample":
        _sample(args)
    elif args.cmd == "notices":
        _notices_cmd(args)
    elif args.cmd == "fetch":
        _fetch(args)
    elif args.cmd == "match":
        asyncio.run(_match(args))
    elif args.cmd == "shred":
        asyncio.run(_shred(args))
    else:
        _score(args)


if __name__ == "__main__":
    main()
