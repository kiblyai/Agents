"""Command line: `python -m freightq sample | inbox | price | run | score`."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from agentkit.env import load_dotenv
from agentkit.llm import LLM

from .broker import EQUIPMENT_LABELS, load_broker
from .extract import Shipment, canonical_equipment, norm_state, norm_zip, parse_date
from .inbox import read_inbox
from .output import (LOAD_COLUMNS, REVIEW_COLUMNS, load_rows, review_rows, summary, write_csv, write_json,
                     write_replies, write_xlsx)
from .pipeline import plural, read_all
from .pricing import load_lanes, price
from .reply import money


@dataclass
class Settings:
    """Model settings, read from the same environment variables as leadagent (FREIGHTQ_* override them)."""

    api_key: str = field(default="", repr=False)
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "openrouter/free"
    fallback_models: list[str] = field(default_factory=list)
    rpm: float = 18.0
    app_title: str = "freightq"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        fallbacks = env("FREIGHTQ_FALLBACK_MODELS") or env("LEADAGENT_FALLBACK_MODELS") or ""
        return cls(
            api_key=env("OPENROUTER_API_KEY", ""),
            base_url=env("FREIGHTQ_BASE_URL") or env("LEADAGENT_BASE_URL") or cls.base_url,
            model=env("FREIGHTQ_MODEL") or env("LEADAGENT_MODEL") or cls.model,
            fallback_models=[m.strip() for m in fallbacks.split(",") if m.strip()],
            rpm=float(env("FREIGHTQ_RPM") or env("LEADAGENT_RPM") or cls.rpm),
        )


def _require(*paths: str | None) -> None:
    missing = [p for p in paths if p and not Path(p).exists()]
    if missing:
        sys.exit("Not found: " + ", ".join(missing) + f"\n(current folder: {Path.cwd()})")


def _broker(path: str | None):
    _require(path)
    try:
        return load_broker(path)
    except ValueError as e:
        sys.exit(str(e))


def _lanes(path: str | None):
    if not path:
        print("No --lanes file: every lane is left for pricing by hand.")
        return [], []
    _require(path)
    try:
        loads, problems = load_lanes(path)
    except ValueError as e:
        sys.exit(str(e))
    print(f"Lane history: {plural(len(loads), 'past load')} from {path}"
          + (f" ({plural(len(problems), 'row')} skipped; see run_summary.json)" if problems else ""))
    return loads, problems


def _inbox(args):
    _require(args.emails)
    emails, skipped = read_inbox(args.emails)
    print(f"Inbox: {plural(len(emails), 'email')} in {args.emails}")
    for e in emails:
        sent = e.sent.strftime("%a %Y-%m-%d %H:%M") if e.sent else "no date"
        extra = f"; attachments: {', '.join(a.name for a in e.attachments)}" if e.attachments else ""
        extra += f"; unreadable: {', '.join(e.skipped)}" if e.skipped else ""
        print(f"  {e.file}: {sent}, {e.sender or 'no sender'}, \"{e.subject}\"{extra}")
    for s in skipped:
        print(f"  skipped {s}")
    return emails


def _inbox_cmd(args) -> None:
    emails = _inbox(args)
    print(f"A run needs {plural(len(emails), 'model request')} (free models allow 50 a day, 1,000 after $10 of credit).")


PLACE_RE = re.compile(r"^\s*(.*?)[,\s]+([A-Za-z]{2})\.?(?:\s+(\d{5}))?\s*$")


def _place_arg(s: str) -> dict:
    """"Dallas, TX 75207", "Dallas, TX" or "75207"."""
    if re.fullmatch(r"\s*\d{5}\s*", s):
        return {"city": "", "state": "", "zip": norm_zip(s)}
    m = PLACE_RE.match(s)
    if not m or not norm_state(m.group(2)):
        sys.exit(f"Could not read the place '{s}'. Use \"City, ST\", \"City, ST 12345\" or a ZIP code.")
    return {"city": m.group(1).strip(), "state": norm_state(m.group(2)), "zip": m.group(3) or ""}


def _price_cmd(args) -> None:
    cfg = _broker(args.broker)
    loads, _ = _lanes(args.lanes)
    eq = canonical_equipment(args.equipment)
    if not eq:
        sys.exit(f"Unknown equipment '{args.equipment}'. Use one of: {', '.join(EQUIPMENT_LABELS)}.")
    asof = parse_date(args.date) if args.date else date.today()
    s = Shipment(kind="quote_request", customer=args.customer or "", origin=_place_arg(args.origin),
                 destination=_place_arg(args.dest), equipment=eq)
    q = price(s, loads, cfg, asof)
    print(f"{s.lane_text()}, {EQUIPMENT_LABELS[eq]}, as of {asof}")
    for x in q.comparables:
        rate = f", customer paid {money(x.customer_rate)}" if x.customer_rate else ""
        print(f"  {x.day} {x.lane_text()}: carrier pay {money(x.carrier_pay)}{rate} ({x.customer or 'no customer'}, "
              f"row {x.row})")
    if q.priced:
        print(f"Median carrier pay {money(q.buy)} -> suggested linehaul {money(q.linehaul)} "
              f"({100 * q.margin:.0f}% margin), from {q.basis}")
    for f in s.flags:
        print(f"Check: {f.text}")


async def _run(args) -> None:
    s = Settings.from_env()
    if args.model:
        s.model = args.model
    if not s.api_key:
        sys.exit("OPENROUTER_API_KEY is not set. Put it in your environment or a local .env file.")
    if s.model.endswith(":free") or s.model == "openrouter/free":
        print("WARNING: free models may log prompts. Use them only with the synthetic example, never a brokerage's "
              "real emails or rates. For client work set FREIGHTQ_MODEL to a paid model and turn on zero data "
              "retention in your OpenRouter privacy settings.\n")
    cfg = _broker(args.broker)
    loads, problems = _lanes(args.lanes)
    emails = _inbox(args)
    if not emails:
        sys.exit("No .eml or .txt emails in the folder.")
    if args.limit:
        emails = emails[:args.limit]
    today = parse_date(args.today) if args.today else None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Model {s.model}; reading {plural(len(emails), 'email')}")

    llm = LLM.from_settings(s)
    run = await read_all(emails, cfg, loads, llm, out / "cache", today=today)
    replies = write_replies(run, cfg, out / "replies")
    review = review_rows(run)
    write_csv(out / "review.csv", review, REVIEW_COLUMNS)
    tenders = load_rows(run)
    write_csv(out / "loads.csv", tenders, LOAD_COLUMNS)
    write_xlsx(out / "quotes.xlsx", run)
    write_json(out / "results.json", run)
    stats = summary(run, llm.usage, len(loads), problems)
    (out / "run_summary.json").write_text(json.dumps(stats, indent=2))

    print(f"\nDone: {plural(stats['quote_requests'], 'quote request')}, {plural(stats['tenders'], 'tender')}, "
          f"{stats['other']} other; "
          f"{stats['lanes_priced']} of {plural(stats['lanes'], 'lane')} priced; {plural(replies, 'reply', 'replies')} "
          f"drafted; {plural(stats['llm_requests'], 'model request')}; ${stats['cost_usd']:.4f}")
    if run.stopped_reason:
        print("Stopped early: " + run.stopped_reason)
    if run.errors:
        print(f"{plural(run.errors, 'email')} failed; re-run to retry them (finished emails come from the cache).")
    print(f"Check first: {out / 'review.csv'} ({plural(len(review), 'item')})")
    print(f"Files: {out / 'replies'}/, {out / 'quotes.xlsx'}, {out / 'loads.csv'} ({plural(len(tenders), 'tender')})")


def _sample(args) -> None:
    from .sample import make_sample

    out = Path(args.out)
    truth = make_sample(out)
    lanes = sum(len(e["shipments"]) for e in truth["emails"])
    print(f"Wrote {plural(len(truth['emails']), 'email')} ({plural(lanes, 'lane')}) to {out / 'inbox'}, plus "
          f"{out / 'lanes.csv'}, {out / 'broker.toml'} and the answer key {out / 'truth.json'}")


def _score(args) -> None:
    from .sample import format_score, score

    _require(args.run, args.truth)
    results = json.loads((Path(args.run) / "results.json").read_text())
    truth = json.loads(Path(args.truth).read_text())
    print(format_score(score(results, truth)))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="freightq", description="Read shipper emails into priced quote drafts and loads.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sm = sub.add_parser("sample", help="write the synthetic inbox (fictional companies) with lane history and answer key")
    sm.add_argument("--out", default="examples/freightq", help="folder (default examples/freightq)")

    ib = sub.add_parser("inbox", help="list the emails found and what a run needs (no model calls)")
    ib.add_argument("--emails", required=True, help="folder of .eml or .txt emails")

    pr = sub.add_parser("price", help="price one lane from the lane history (no model calls)")
    pr.add_argument("--from", dest="origin", required=True, help='origin, e.g. "Dallas, TX 75207"')
    pr.add_argument("--to", dest="dest", required=True, help='destination, e.g. "Atlanta, GA"')
    pr.add_argument("--equipment", default="van", help="van, reefer, flatbed, ... (default van)")
    pr.add_argument("--lanes", required=True, help="lane history CSV (past loads with carrier pay)")
    pr.add_argument("--broker", help="brokerage settings (TOML); defaults otherwise")
    pr.add_argument("--customer", help="customer name, to compare with what they paid before")
    pr.add_argument("--date", help="price as of this date, YYYY-MM-DD (default today)")

    r = sub.add_parser("run", help="read the emails, price the lanes and draft replies")
    r.add_argument("--emails", required=True, help="folder of .eml or .txt emails")
    r.add_argument("--lanes", help="lane history CSV (past loads with carrier pay)")
    r.add_argument("--broker", help="brokerage settings (TOML); defaults otherwise")
    r.add_argument("--out", default="out/freightq", help="output folder (default out/freightq)")
    r.add_argument("--model", help="model id (default FREIGHTQ_MODEL, then LEADAGENT_MODEL)")
    r.add_argument("--limit", type=int, help="only the first N emails (for a quick test)")
    r.add_argument("--today", help="read emails without a date as sent on this day, YYYY-MM-DD (default today)")

    sc = sub.add_parser("score", help="compare a run on the synthetic inbox with its answer key")
    sc.add_argument("--run", default="out/freightq", help="the run's output folder (default out/freightq)")
    sc.add_argument("--truth", default="examples/freightq/truth.json", help="answer key from `sample`")

    args = p.parse_args(argv)
    load_dotenv()
    if args.cmd == "sample":
        _sample(args)
    elif args.cmd == "inbox":
        _inbox_cmd(args)
    elif args.cmd == "price":
        _price_cmd(args)
    elif args.cmd == "run":
        asyncio.run(_run(args))
    else:
        _score(args)


if __name__ == "__main__":
    main()
