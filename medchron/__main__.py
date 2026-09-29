"""Command line: `python -m medchron sample | pages | run | score`."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from agentkit.env import load_dotenv
from agentkit.llm import LLM

from .chronology import REVIEW_COLUMNS, build
from .extract import parse_date
from .output import bookmarks, summary, write_csv, write_docx, write_json, write_xlsx
from .pipeline import Options, extract_all, make_batches, plural
from .records import read_case, write_combined


@dataclass
class Settings:
    """Model settings: MEDCHRON_* variables, falling back to the LEADAGENT_* ones for OpenRouter test runs."""

    api_key: str = field(default="", repr=False)
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "openrouter/free"
    fallback_models: list[str] = field(default_factory=list)
    rpm: float = 18.0
    app_title: str = "medchron"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        fallbacks = env("MEDCHRON_FALLBACK_MODELS") or env("LEADAGENT_FALLBACK_MODELS") or ""
        return cls(
            api_key=env("MEDCHRON_API_KEY") or env("OPENROUTER_API_KEY", ""),
            base_url=env("MEDCHRON_BASE_URL") or env("LEADAGENT_BASE_URL") or cls.base_url,
            model=env("MEDCHRON_MODEL") or env("LEADAGENT_MODEL") or cls.model,
            fallback_models=[m.strip() for m in fallbacks.split(",") if m.strip()],
            rpm=float(env("MEDCHRON_RPM") or env("LEADAGENT_RPM") or cls.rpm),
        )


def privacy_problem(s: Settings, synthetic: bool) -> str:
    """Why this run must not go ahead with real records, or "" if it may."""
    if synthetic:
        return ""
    if s.model.endswith(":free") or s.model == "openrouter/free":
        return ("Free models may log prompts, so they are only for synthetic records. Add --synthetic if these are "
                "test records; never send a real patient's records to a free model.")
    if os.environ.get("MEDCHRON_BAA", "").strip().lower() not in ("yes", "true", "1"):
        return ("Real medical records are protected health information (HIPAA). Send them only to a model provider "
                "that has signed a business associate agreement (BAA) with you, then set MEDCHRON_BAA=yes. "
                "If these are synthetic test records, add --synthetic.")
    return ""


def _require(*paths: str | None) -> None:
    missing = [p for p in paths if p and not Path(p).exists()]
    if missing:
        sys.exit("Not found: " + ", ".join(missing) + f"\n(current folder: {Path.cwd()})")


def _read(args, ocr_dir: Path | None = None):
    _require(args.records)
    try:
        case = read_case(args.records, ocr=getattr(args, "ocr", False), ocr_dir=ocr_dir)
    except RuntimeError as e:
        sys.exit(str(e))
    print(f"Records: {plural(len(case.pages), 'page')} from {plural(len(case.files_read), 'file')}")
    for f in case.files_read:
        print(f"  {f}")
    for f in case.files_skipped:
        print(f"  skipped {f}")
    blank = sum(1 for p in case.pages if p.needs_ocr)
    if blank:
        print(f"{blank} pages have no text layer (scans). Add --ocr to read them (needs: brew install ocrmypdf).")
    return case


def _pages(args) -> None:
    case = _read(args)
    batches = make_batches(case.pages, Options(batch_pages=args.batch))
    print(f"About {len(batches)} model requests at up to {args.batch} pages each (free models allow 50 a day, "
          "1,000 after $10 of credit).")


async def _run(args) -> None:
    s = Settings.from_env()
    if args.model:
        s.model = args.model
    problem = privacy_problem(s, args.synthetic)
    if problem:
        sys.exit(problem)
    if not s.api_key:
        sys.exit("No API key. Set OPENROUTER_API_KEY (or MEDCHRON_API_KEY) in your environment or a local .env file.")
    doi = parse_date(args.doi) if args.doi else None
    if args.doi and doi is None:
        sys.exit(f"Could not read the date of injury '{args.doi}'. Use YYYY-MM-DD, e.g. 2025-03-14.")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    case = _read(args, ocr_dir=out / "ocr")
    if not case.pages:
        sys.exit("No readable PDF pages in the records folder.")
    print(f"Model {s.model}; {args.batch} pages per request" + (f"; first {args.limit} pages only" if args.limit else ""))

    llm = LLM.from_settings(s)
    opts = Options(patient=args.patient or "", doi=doi, batch_pages=args.batch, gap_days=args.gap_days)
    run = await extract_all(case, opts, llm, out / "cache", limit=args.limit)
    chron = build(run.entries, run.bills, case, doi=doi, gap_days=args.gap_days, spot_check=opts.spot_check,
                  no_content=run.no_content, other_patient=run.other_patient, not_processed=run.not_processed)

    combined = out / "records_combined.pdf"
    write_combined(case, combined, bookmarks(chron))
    write_docx(out / "chronology.docx", chron, case, patient=args.patient or "", doi=doi, combined_name=combined.name)
    write_xlsx(out / "chronology.xlsx", chron, case)
    write_csv(out / "review.csv", chron.review, REVIEW_COLUMNS)
    write_json(out / "chronology.json", chron)
    stats = summary(run, chron, case, llm.usage)
    (out / "run_summary.json").write_text(json.dumps(stats, indent=2))

    print(f"\nDone: {plural(stats['entries'], 'entry', 'entries')} ({stats['entries_need_review']} to check, "
          f"{stats['entries_before_injury']} before the injury), {plural(stats['treatment_gaps'], 'treatment gap')}, "
          f"{plural(stats['bill_lines'], 'bill line')} totalling ${stats['total_billed']:,.2f}; "
          f"{plural(stats['llm_requests'], 'model request')}; ${stats['cost_usd']:.4f}")
    if run.stopped_reason:
        print("Stopped early: " + run.stopped_reason)
    if run.batch_errors:
        print(f"{len(run.batch_errors)} batches failed; re-run to retry them (finished batches come from the cache).")
    print(f"Check first: {out / 'review.csv'} ({len(chron.review)} items)")
    print(f"Files: {out / 'chronology.docx'}, {out / 'chronology.xlsx'}, {combined}")


def _sample(args) -> None:
    from .sample import make_sample

    truth = make_sample(Path(args.out), pt_visits=args.pt_visits)
    print(f"Wrote {truth['pages']} pages of synthetic records to {Path(args.out) / 'records'} and the answer key to "
          f"{Path(args.out) / 'truth.json'}")
    print(f"Patient {truth['patient']}, date of injury {truth['doi']}: {len(truth['encounters'])} encounters, "
          f"{plural(len(truth['gaps']), 'treatment gap')}, ${truth['total_billed']:,.2f} billed")


def _score(args) -> None:
    from .sample import format_score, score

    _require(args.run, args.truth)
    chron = json.loads((Path(args.run) / "chronology.json").read_text())
    truth = json.loads(Path(args.truth).read_text())
    print(format_score(score(chron, truth)))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="medchron", description="Build a cited medical chronology from medical records.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sm = sub.add_parser("sample", help="write a synthetic case file (fictional patient) with its answer key")
    sm.add_argument("--out", default="examples/medchron", help="folder (default examples/medchron)")
    sm.add_argument("--pt-visits", type=int, default=8, help="physical-therapy visits, 1 page each (default 8, up to 120)")

    pg = sub.add_parser("pages", help="list the files and pages found, and the requests a run needs (no model calls)")
    pg.add_argument("--records", required=True, help="folder of the case's PDF records")
    pg.add_argument("--batch", type=int, default=5, help="pages per model request (default 5)")

    r = sub.add_parser("run", help="build the chronology")
    r.add_argument("--records", required=True, help="folder of the case's PDF records (read in file-name order)")
    r.add_argument("--patient", help="patient's name, used to spot another patient's pages")
    r.add_argument("--doi", help="date of injury, YYYY-MM-DD: marks earlier records and starts the gap check")
    r.add_argument("--out", default="out/medchron", help="output folder (default out/medchron)")
    r.add_argument("--synthetic", action="store_true", help="these are synthetic test records, not a real patient's")
    r.add_argument("--ocr", action="store_true", help="add a text layer to scanned pages first (needs ocrmypdf)")
    r.add_argument("--model", help="model id (default MEDCHRON_MODEL, then LEADAGENT_MODEL)")
    r.add_argument("--batch", type=int, default=5, help="pages per model request (default 5)")
    r.add_argument("--gap-days", type=int, default=30, help="report treatment gaps longer than this (default 30)")
    r.add_argument("--limit", type=int, help="only the first N pages (for a quick test)")

    sc = sub.add_parser("score", help="compare a run on the synthetic case with its answer key")
    sc.add_argument("--run", default="out/medchron", help="the run's output folder (default out/medchron)")
    sc.add_argument("--truth", default="examples/medchron/truth.json", help="answer key from `sample`")

    args = p.parse_args(argv)
    load_dotenv()
    if args.cmd == "sample":
        _sample(args)
    elif args.cmd == "pages":
        _pages(args)
    elif args.cmd == "run":
        asyncio.run(_run(args))
    else:
        _score(args)


if __name__ == "__main__":
    main()
