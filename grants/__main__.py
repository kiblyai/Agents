"""Command line: `python -m grants sample | funders | prospect | draft | score`."""

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

from .draft import load_docs, load_request, read_budget
from .funders import apply_rules, read_funders
from .org import load_org
from .output import write_draft, write_prospects
from .pipeline import CACHE_VERSION, draft_letter, prospect_all


def _org(path):
    require(path)
    try:
        return load_org(path)
    except ValueError as e:
        sys.exit(str(e))


def _today(args) -> date:
    if not args.today:
        return date.today()
    d = parse_date(args.today)
    if d is None:
        sys.exit(f"Could not read --today '{args.today}'; use YYYY-MM-DD.")
    return d


def _funders(args):
    require(args.funders, args.guidelines)
    try:
        return read_funders(args.funders, args.guidelines)
    except ValueError as e:
        sys.exit(str(e))


def _llm(args):
    s = Settings.from_env("GRANTS", "grants")
    if args.model:
        s.model = args.model
    check_key(s)
    if is_free_model(s.model):
        print(free_model_warning("GRANTS", "a nonprofit's internal documents"))
    return s, LLM.from_settings(s)


def _funders_cmd(args) -> None:
    org, today = _org(args.org), _today(args)
    funders = _funders(args)
    left = 0
    for f in funders:
        apply_rules(f, org, today)
        left += not f.out
        state = "ruled out: " + "; ".join(f.out) if f.out else "to score"
        print(f"  {f.name}: {state}" + (f" ({'; '.join(f.notes)})" if f.notes else "")
              + ("" if f.guidelines or f.out else " - no guidelines text; add them for a better score"))
    print(f"{len(funders)} funders; {left} left to score = {plural(left, 'model request')}.")


async def _prospect(args) -> None:
    org, today = _org(args.org), _today(args)
    funders = _funders(args)
    s, llm = _llm(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Model {s.model}; checking {plural(len(funders), 'funder')} for {org.name}")
    runner = Runner(llm, out / "cache", CACHE_VERSION, hint="set GRANTS_FALLBACK_MODELS")
    prospects = await prospect_all(funders, org, runner, today)
    rows = write_prospects(out, prospects)
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("strong", "possible", "weak", "ruled out",
                                                                     "not scored")}
    summary = {**counts, "llm_requests": llm.usage.requests, "cost_usd": round(llm.usage.cost_usd, 6),
               "prompt_tokens": llm.usage.prompt_tokens, "completion_tokens": llm.usage.completion_tokens,
               "models_used": llm.usage.models, "from_cache": runner.from_cache, "stopped_reason": runner.stopped_reason}
    (out / "prospects_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nDone: {counts['strong']} strong, {counts['possible']} possible, {counts['weak']} weak, "
          f"{counts['ruled out']} ruled out; {plural(llm.usage.requests, 'model request')}; ${llm.usage.cost_usd:.4f}")
    if runner.stopped_reason:
        print("Stopped early: " + runner.stopped_reason)
    print(f"Ranked list: {out / 'prospects.csv'} (and prospects.xlsx)")


async def _draft(args) -> None:
    org, today = _org(args.org), _today(args)
    require(args.request, args.docs, args.budget)
    try:
        req = load_request(args.request)
    except ValueError as e:
        sys.exit(str(e))
    funders = _funders(args)
    funder = next((f for f in funders if f.name.lower() == req.funder.lower()), None)
    if funder is None:
        sys.exit(f"'{req.funder}' is not in {args.funders}; the request's funder must match a name there.")
    if org.program(req.program) is None:
        sys.exit(f"'{req.program}' is not one of the programs in {args.org}.")
    lib = load_docs(args.docs)
    budget = read_budget(args.budget)
    print(f"Documents: {plural(len(lib.passages), 'passage')} from {', '.join(lib.files_read) or 'nothing'}")
    for x in lib.files_skipped:
        print(f"  skipped {x}")
    s, llm = _llm(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runner = Runner(llm, out / "cache", CACHE_VERSION, hint="set GRANTS_FALLBACK_MODELS")
    others = [f.name for f in funders]
    d, ev = await draft_letter(req, org, funder, lib, budget, others, runner, today)
    if d.error:
        sys.exit(f"No draft: {d.error}" + (f"\n{runner.stopped_reason}" if runner.stopped_reason else ""))
    files = write_draft(out, d, org, funder, ev, today)
    holds = sum(1 for f in d.flags if f.hold)
    print(f"\nDone: {d.request.kind.lower()} to {funder.name}, {d.words} words, "
          + ("ready to edit" if d.ready else f"{holds} to fix") + f"; {plural(llm.usage.requests, 'model request')}; "
          f"${llm.usage.cost_usd:.4f}")
    print(f"Draft: {files['docx']} (the last page lists the checks and the source of each section)")
    print(f"Check first: {out / 'review_draft.csv'} ({plural(files['review_items'], 'item')})")


def _sample(args) -> None:
    from .sample import make_sample

    out = Path(args.out)
    truth = make_sample(out)
    print(f"Wrote {out / 'org.toml'}, docs/, budget.csv, funders.csv ({len(truth['funders'])} funders), guidelines/, "
          f"request.toml and the answer key {out / 'truth.json'}. Use --today {truth['today']} with this example.")


def _score(args) -> None:
    from .sample import format_score, score

    require(args.run, args.truth)
    run = Path(args.run)

    def load(name):
        p = run / name
        return json.loads(p.read_text()) if p.exists() else None

    print(format_score(score(load("prospects.json"), load("draft.json"), json.loads(Path(args.truth).read_text()))))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="grants", description="Grant prospecting and checked first drafts for nonprofits.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sm = sub.add_parser("sample", help="write the synthetic nonprofit and funders (fictional) with the answer key")
    sm.add_argument("--out", default="examples/grants", help="folder (default examples/grants)")

    def base(sp):
        sp.add_argument("--org", required=True, help="the nonprofit's profile (TOML)")
        sp.add_argument("--funders", required=True, help="funder list (CSV/Excel)")
        sp.add_argument("--guidelines", help="folder of funder guidelines (one file per funder, named like the funder)")
        sp.add_argument("--today", help="treat this date as today, YYYY-MM-DD (for deadlines)")

    f = sub.add_parser("funders", help="apply the hard rules to the funder list (no model calls)")
    base(f)

    pr = sub.add_parser("prospect", help="rule out and score funders, best first")
    base(pr)
    pr.add_argument("--out", default="out/grants", help="output folder (default out/grants)")
    pr.add_argument("--model", help="model id (default GRANTS_MODEL, then LEADAGENT_MODEL)")

    d = sub.add_parser("draft", help="draft a letter of inquiry from the nonprofit's documents")
    base(d)
    d.add_argument("--request", required=True, help="what to draft (TOML: funder, program, amount, sections)")
    d.add_argument("--docs", required=True, help="folder of the nonprofit's documents (PDF, Word, Markdown, text)")
    d.add_argument("--budget", help="program budget CSV: Program, Item, Amount")
    d.add_argument("--out", default="out/grants", help="output folder (default out/grants)")
    d.add_argument("--model", help="model id (default GRANTS_MODEL, then LEADAGENT_MODEL)")

    sc = sub.add_parser("score", help="compare a run on the synthetic example with its answer key")
    sc.add_argument("--run", default="out/grants", help="the run's output folder (default out/grants)")
    sc.add_argument("--truth", default="examples/grants/truth.json", help="answer key from `sample`")

    args = p.parse_args(argv)
    load_dotenv()
    if args.cmd == "sample":
        _sample(args)
    elif args.cmd == "funders":
        _funders_cmd(args)
    elif args.cmd == "prospect":
        asyncio.run(_prospect(args))
    elif args.cmd == "draft":
        asyncio.run(_draft(args))
    else:
        _score(args)


if __name__ == "__main__":
    main()
