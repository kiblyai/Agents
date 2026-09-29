"""Command line: `python -m secq inspect ...` and `python -m secq run ...`."""

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

from .docs import load_kb, write_csv_rows
from .pipeline import REVIEW_COLUMNS, Options, draft_all, review_rows, summary
from .sheet import column_number, open_workbook, read_questions, write_draft


@dataclass
class Settings:
    """Model settings, read from the same environment variables as leadagent (SECQ_* override them)."""

    api_key: str = field(default="", repr=False)
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "openrouter/free"
    fallback_models: list[str] = field(default_factory=list)
    rpm: float = 18.0
    app_title: str = "secq"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        fallbacks = env("SECQ_FALLBACK_MODELS") or env("LEADAGENT_FALLBACK_MODELS") or ""
        return cls(
            api_key=env("OPENROUTER_API_KEY", ""),
            base_url=env("SECQ_BASE_URL") or env("LEADAGENT_BASE_URL") or cls.base_url,
            model=env("SECQ_MODEL") or env("LEADAGENT_MODEL") or cls.model,
            fallback_models=[m.strip() for m in fallbacks.split(",") if m.strip()],
            rpm=float(env("SECQ_RPM") or env("LEADAGENT_RPM") or cls.rpm),
        )


def _layout_override(args) -> dict | None:
    o = {"q_col": column_number(args.question_col), "a_col": column_number(args.answer_col),
         "c_col": column_number(args.comment_col), "header_row": args.header_row}
    return o if any(v is not None for v in o.values()) else None


def _require(*paths: str | None) -> None:
    missing = [p for p in paths if p and not Path(p).exists()]
    if missing:
        sys.exit("Not found: " + ", ".join(missing) + f"\n(current folder: {Path.cwd()})")


def _inspect(args) -> None:
    _require(args.questionnaire)
    wb = open_workbook(args.questionnaire)
    layouts, questions = read_questions(wb, args.sheet, _layout_override(args))
    if not layouts:
        sys.exit("No question/answer columns found. Use --question-col and --answer-col (e.g. B and C), "
                 "and --header-row if the header is not near the top.")
    for lay in layouts.values():
        print(lay.describe())
    todo = [q for q in questions if not q.prefilled]
    print(f"\n{len(questions)} questions ({len(questions) - len(todo)} already answered). First few:")
    for q in questions[:8]:
        print(f"  {q.key:>12}  {('[' + q.section + '] ') if q.section else ''}{q.text[:90]}")


async def _run(args) -> None:
    _require(args.questionnaire, args.kb)
    s = Settings.from_env()
    if args.model:
        s.model = args.model
    if not s.api_key:
        sys.exit("OPENROUTER_API_KEY is not set. Put it in your environment or a local .env file.")
    if s.model.endswith(":free") or s.model == "openrouter/free":
        print("WARNING: free models may log prompts. Use them only with sample documents, never a client's real "
              "security documents. For client work set SECQ_MODEL to a paid model and turn on zero data retention "
              "in your OpenRouter privacy settings.\n")
    kb = load_kb(args.kb)
    print(f"Knowledge base: {len(kb.passages)} passages, {len(kb.library)} past answers from {len(kb.files_read)} files")
    for f in kb.files_skipped:
        print(f"  skipped {f}")
    if not kb.passages and not kb.library:
        sys.exit("Nothing readable in the knowledge-base folder.")

    wb = open_workbook(args.questionnaire)
    layouts, questions = read_questions(wb, args.sheet, _layout_override(args))
    if not layouts:
        sys.exit("No question/answer columns found; run `python -m secq inspect` and pass the columns explicitly.")
    todo = [q for q in questions if args.overwrite or not q.prefilled]
    if args.limit:
        todo = todo[: args.limit]
    print(f"Questionnaire: {len(questions)} questions, drafting {len(todo)}; model {s.model}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    llm = LLM.from_settings(s)
    opts = Options(company=args.company, batch_size=args.batch, top_k=args.top_k)
    run = await draft_all(todo, kb, opts, llm, out / "cache")

    stem = Path(args.questionnaire).stem
    draft_path = out / f"{stem}_draft.xlsx"
    write_draft(args.questionnaire, draft_path, layouts, run.results)
    write_csv_rows(out / "review.csv", review_rows(run.results), REVIEW_COLUMNS)
    stats = summary(run, kb, len(questions), len(questions) - len([q for q in questions if not q.prefilled]), llm.usage)
    (out / "run_summary.json").write_text(json.dumps(stats, indent=2))
    print(f"\nDone: {stats['drafted']} drafted, {stats['needs_review']} need review "
          f"(high {stats['confidence']['high']}, medium {stats['confidence']['medium']}, low {stats['confidence']['low']}); "
          f"{stats['llm_requests']} model requests; ${stats['cost_usd']:.4f}")
    if run.stopped_reason:
        print("Stopped early: " + run.stopped_reason)
    print(f"Files: {draft_path} (yellow rows need review), {out / 'review.csv'}, {out / 'run_summary.json'}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="secq", description="Draft security-questionnaire answers from your own documents.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def layout_args(sp):
        sp.add_argument("--questionnaire", required=True, help="the questionnaire (.xlsx or .csv)")
        sp.add_argument("--sheet", help="only this sheet (default: every sheet with questions)")
        sp.add_argument("--question-col", help="column letter of the questions, if not detected")
        sp.add_argument("--answer-col", help="column letter for answers (e.g. Yes/No)")
        sp.add_argument("--comment-col", help="column letter for explanations/comments")
        sp.add_argument("--header-row", type=int, help="row number of the column headers")

    i = sub.add_parser("inspect", help="show which columns and questions were found (no model calls)")
    layout_args(i)
    r = sub.add_parser("run", help="draft answers")
    layout_args(r)
    r.add_argument("--kb", required=True, help="folder of the company's documents (pdf, docx, md, txt, past xlsx/csv)")
    r.add_argument("--company", required=True, help="company name to answer as")
    r.add_argument("--out", default="out/secq", help="output folder (default out/secq)")
    r.add_argument("--model", help="model id (default SECQ_MODEL, then LEADAGENT_MODEL)")
    r.add_argument("--batch", type=int, default=8, help="questions per model request (default 8)")
    r.add_argument("--top-k", type=int, default=4, help="document passages per question (default 4)")
    r.add_argument("--limit", type=int, help="only the first N questions (for a quick test)")
    r.add_argument("--overwrite", action="store_true", help="also redo questions that already have an answer")

    args = p.parse_args(argv)
    load_dotenv()
    if args.cmd == "inspect":
        _inspect(args)
    else:
        asyncio.run(_run(args))


if __name__ == "__main__":
    main()
