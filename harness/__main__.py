"""Command line: `python -m harness run | score | history`.

Runs the agents on their examples, scores each against its answer key and keeps a history so runs, models and code
changes can be compared. Outputs go to out/harness/<model>/<agent>/; the agents' model caches live there too, so a run
stopped by the daily free limit continues where it left off.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .agents import AGENTS, ROOT, Score

OUT = ROOT / "out" / "harness"
HISTORY = OUT / "history.jsonl"
PASS_PCT = 90.0


def slug(model: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", model).strip("-") if model else "default"


def _commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                               capture_output=True, text=True).stdout.strip()
        return sha + ("+changes" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return ""


def run_agent(name: str, out: Path, model: str | None) -> int:
    """Run `python -m <name> run` on the example, echoing its output and saving it to run.log."""
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", name, "run", *AGENTS[name].args(out)] + (["--model", model] if model else [])
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    with open(out / "run.log", "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            print(f"  {line}", end="")
            log.write(line)
        return proc.wait()


def status_of(out: Path, name: str, returncode: int | None = 0) -> str:
    """complete, incomplete (stopped early or some batches failed) or crashed (nothing to score)."""
    if returncode or not (out / AGENTS[name].output).exists():
        return "crashed"
    try:
        s = json.loads((out / "run_summary.json").read_text())
    except (OSError, ValueError):
        return "complete"
    if (s.get("stopped_early") or s.get("stopped_reason") or s.get("not_processed") or s.get("batch_errors")
):
        return "incomplete"
    return "complete"


def verdict(status: str, sc: Score | None) -> str:
    if status != "complete" or sc is None:
        return status.upper()
    return "PASS" if not sc.errors and sc.pct >= PASS_PCT else "FAIL"


def evaluate(name: str, out: Path, returncode: int | None = 0) -> dict:
    status = status_of(out, name, returncode)
    sc = None
    if status != "crashed":
        try:
            sc = AGENTS[name].score(out)
        except Exception as e:  # a half-written output must not stop the other agents
            status = "crashed"
            print(f"  could not score {name}: {type(e).__name__}: {e}")
    try:
        s = json.loads((out / "run_summary.json").read_text())
    except (OSError, ValueError):
        s = {}
    return {
        "agent": name, "status": status, "verdict": verdict(status, sc), "score": sc,
        "requests": s.get("llm_requests", 0), "cost_usd": s.get("cost_usd", 0.0), "models": s.get("models_used", {}),
        "stopped": s.get("stopped_early") or s.get("stopped_reason") or "", "out": out,
    }


def record(r: dict, model: str | None) -> dict:
    sc: Score | None = r["score"]
    return {
        "time": datetime.now().isoformat(timespec="seconds"), "commit": _commit(), "model": model or "default",
        "agent": r["agent"], "verdict": r["verdict"], "got": sc.got if sc else 0, "total": sc.total if sc else 0,
        "pct": round(sc.pct, 1) if sc else 0.0, "errors": sc.errors if sc else [],
        "checks": sc.checks if sc else [], "requests": r["requests"], "cost_usd": r["cost_usd"],
        "models_used": r["models"],
    }


def load_history() -> list[dict]:
    if not HISTORY.exists():
        return []
    return [json.loads(line) for line in HISTORY.read_text().splitlines() if line.strip()]


def report(results: list[dict], previous: dict[str, dict]) -> str:
    lines = [f"{'agent':<10} {'result':<11} {'score':<16} {'errors':>6} {'requests':>8} {'cost':>9}  vs last run"]
    for r in results:
        sc: Score | None = r["score"]
        score = f"{sc.got}/{sc.total} ({sc.pct:.0f}%)" if sc else "-"
        prev = previous.get(r["agent"])
        vs = f"{prev['pct']:.0f}% -> {sc.pct:.0f}%" if prev and sc else ""
        lines.append(f"{r['agent']:<10} {r['verdict']:<11} {score:<16} {len(sc.errors) if sc else '-':>6} "
                     f"{r['requests']:>8} ${r['cost_usd']:>8.4f}  {vs}")
    for r in results:
        sc = r["score"]
        detail = [f"  {label}: {got} of {total}" for label, got, total in (sc.checks if sc else []) if got < total]
        detail += [f"  ERROR {e}" for e in (sc.errors if sc else [])]
        detail += [f"  - {n}" for n in (sc.notes[:12] if sc else [])]
        if sc and len(sc.notes) > 12:
            detail.append(f"  - and {len(sc.notes) - 12} more")
        if r["stopped"]:
            detail.append(f"  stopped early: {r['stopped'][:200]}")
        if r["status"] == "crashed":
            detail.append(f"  crashed; see {r['out'] / 'run.log'}")
        if detail:
            lines += ["", f"{r['agent']}:"] + detail
    lines += ["", f"PASS = run complete, no errors and at least {PASS_PCT:.0f}% of checks right. "
                  "Errors are outputs that must never happen (invented codes, wrong rates, an unflagged wrong answer)."]
    return "\n".join(lines)


def _agents(arg: str) -> list[str]:
    names = [a.strip() for a in arg.split(",") if a.strip()]
    unknown = [a for a in names if a not in AGENTS]
    if unknown:
        sys.exit(f"Unknown agent: {', '.join(unknown)}. Choose from {', '.join(AGENTS)}.")
    return names


def _last_runs(model: str | None) -> dict[str, dict]:
    last = {}
    for h in load_history():
        if h["model"] == (model or "default") and h["total"]:
            last[h["agent"]] = h
    return last


def _run(args) -> int:
    previous = _last_runs(args.model)
    results = []
    for name in _agents(args.agents):
        out = OUT / slug(args.model) / name
        if args.fresh and out.exists():
            shutil.rmtree(out)
        print(f"\n=== {name} ===")
        rc = run_agent(name, out, args.model)
        r = evaluate(name, out, rc)
        results.append(r)
        OUT.mkdir(parents=True, exist_ok=True)
        with open(HISTORY, "a", encoding="utf-8") as f:
            f.write(json.dumps(record(r, args.model)) + "\n")
    print("\n" + report(results, previous))
    return 0 if all(r["verdict"] == "PASS" for r in results) else 1


def _score(args) -> int:
    results = []
    for name in _agents(args.agents):
        out = Path(args.run) / name if args.run else OUT / slug(args.model) / name
        if not out.exists():
            print(f"{name}: no output in {out}; run `python -m harness run` first")
            continue
        results.append(evaluate(name, out))
    if not results:
        return 1
    print(report(results, {}))
    return 0 if all(r["verdict"] == "PASS" for r in results) else 1


def _history(args) -> int:
    rows = [h for h in load_history() if not args.agent or h["agent"] == args.agent][-args.last:]
    if not rows:
        print("No runs yet. Start with `python -m harness run`.")
        return 0
    print(f"{'time':<19} {'commit':<16} {'agent':<9} {'result':<11} {'score':<14} {'errors':>6} {'cost':>9}  model")
    for h in rows:
        score = f"{h['got']}/{h['total']} ({h['pct']:.0f}%)" if h["total"] else "-"
        print(f"{h['time']:<19} {h['commit']:<16} {h['agent']:<9} {h['verdict']:<11} {score:<14} "
              f"{len(h['errors']):>6} ${h['cost_usd']:>8.4f}  {h['model']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="harness", description="Run the agents on their examples and score them.")
    sub = p.add_subparsers(dest="cmd", required=True)
    every = ",".join(AGENTS)

    r = sub.add_parser("run", help="run the agents on their examples, score them and save the result to the history")
    r.add_argument("--agents", default=every, help=f"comma-separated (default {every})")
    r.add_argument("--model", help="model id for every agent (default: each agent's own setting)")
    r.add_argument("--fresh", action="store_true", help="delete earlier outputs and cached model replies first")

    s = sub.add_parser("score", help="score the latest outputs again, without model calls")
    s.add_argument("--agents", default=every, help=f"comma-separated (default {every})")
    s.add_argument("--model", help="score the outputs of this model's run")
    s.add_argument("--run", help="folder holding one subfolder per agent (default out/harness/<model>)")

    h = sub.add_parser("history", help="list earlier runs")
    h.add_argument("--agent", help="only this agent")
    h.add_argument("--last", type=int, default=20, help="how many runs (default 20)")

    args = p.parse_args(argv)
    return {"run": _run, "score": _score, "history": _history}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
