"""Command line: `python -m outreach sample | leads | write | replies | bill | score`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from agentkit.env import load_dotenv
from agentkit.inbox import read_inbox
from agentkit.llm import LLM
from agentkit.runner import Runner
from agentkit.settings import Settings, check_key, free_model_warning, is_free_model, plural, require
from agentkit.tables import write_csv

from .billing import INVOICE_COLUMNS, bill, read_meetings
from .campaign import load_campaign
from .leads import read_leads, read_suppressed, screen
from .output import usage_stats, write_reply_files, write_sequence_files
from .pipeline import CACHE_VERSION, triage_replies, write_sequences


def _campaign(path: str):
    require(path)
    try:
        return load_campaign(path)
    except ValueError as e:
        sys.exit(str(e))


def _leads(args, c, quiet: bool = False):
    require(args.leads, getattr(args, "suppress", None))
    try:
        leads = read_leads(args.leads)
    except ValueError as e:
        sys.exit(str(e))
    screen(leads, c, read_suppressed(getattr(args, "suppress", None)))
    if not quiet:
        ok = [x for x in leads if not x.held]
        print(f"Leads: {len(leads)} in {args.leads}; {len(ok)} to write, {len(leads) - len(ok)} held back")
        for x in leads:
            if x.held:
                print(f"  held: {x.name or x.email or f'row {x.row}'}: {'; '.join(x.held)}")
        for x in ok:
            for n in x.notes:
                print(f"  note: {x.name}: {n}")
    return leads


def _llm(prefix: str, args, real_data: str):
    s = Settings.from_env(prefix, "outreach")
    if args.model:
        s.model = args.model
    check_key(s)
    if is_free_model(s.model):
        print(free_model_warning(prefix, real_data))
    return s, LLM.from_settings(s)


def _leads_cmd(args) -> None:
    c = _campaign(args.campaign)
    leads = _leads(args, c)
    n = sum(1 for x in leads if not x.held)
    print(f"Writing needs {plural(n, 'model request')} (free models allow 50 a day, 1,000 after $10 of credit).")


async def _write(args) -> None:
    c = _campaign(args.campaign)
    leads = _leads(args, c)
    if args.limit:
        leads = leads[:args.limit]
    s, llm = _llm("OUTREACH", args, "a client's real leads or replies")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Model {s.model}; writing {c.steps}-email sequences")
    runner = Runner(llm, out / "cache", CACHE_VERSION, hint="set OUTREACH_FALLBACK_MODELS")
    seqs = await write_sequences(leads, c, runner)
    stats = write_sequence_files(out, seqs, c)
    summary = {"leads": len(leads), "held_back": sum(1 for x in leads if x.held), "written": sum(1 for q in seqs if q.bodies),
               "ready": stats["ready"], "to_fix": sum(1 for q in seqs if q.bodies and q.held), **usage_stats(llm.usage, runner)}
    (out / "sequences_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nDone: {summary['ready']} of {plural(summary['written'], 'sequence')} ready to send, "
          f"{summary['to_fix']} to fix, {summary['held_back']} leads held back; "
          f"{plural(summary['llm_requests'], 'model request')}; ${summary['cost_usd']:.4f}")
    if runner.stopped_reason:
        print("Stopped early: " + runner.stopped_reason)
    print(f"Check first: {out / 'review_sequences.csv'} ({plural(stats['review_items'], 'item')}); "
          f"read them all in {out / 'sequences.xlsx'}")
    print(f"Import into the sequencer: {out / 'sequences.csv'} (map email_1..email_{c.steps} to the steps)")


async def _replies(args) -> None:
    c = _campaign(args.campaign)
    leads = _leads(args, c, quiet=True)
    require(args.replies)
    emails, skipped = read_inbox(args.replies)
    print(f"Replies: {plural(len(emails), 'email')} in {args.replies}")
    for x in skipped:
        print(f"  skipped {x}")
    if not emails:
        sys.exit("No .eml or .txt replies in the folder.")
    s, llm = _llm("OUTREACH", args, "a client's real leads or replies")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runner = Runner(llm, out / "cache", CACHE_VERSION, hint="set OUTREACH_FALLBACK_MODELS")
    results = await triage_replies(emails, leads, c, runner)
    stats = write_reply_files(out, results, c)
    cats: dict[str, int] = {}
    for r in results:
        key = r.category if not r.error else "not read"
        cats[key] = cats.get(key, 0) + 1
    summary = {"replies": len(results), "categories": cats, "drafts": stats["drafts"],
               "suppressed": stats["suppressed"], "follow_ups": stats["follow_ups"], "new_leads": stats["new_leads"],
               **usage_stats(llm.usage, runner)}
    (out / "replies_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nDone: {plural(len(results), 'reply', 'replies')}: "
          + ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(cats.items(), key=lambda x: -x[1]))
          + f"; {plural(stats['drafts'], 'draft')}; {plural(summary['llm_requests'], 'model request')}; "
          f"${summary['cost_usd']:.4f}")
    if runner.stopped_reason:
        print("Stopped early: " + runner.stopped_reason)
    print(f"Check first: {out / 'triage.csv'} (hot replies at the top); drafts in {out / 'drafts'}/")
    print(f"Upload to the sequencer today: {out / 'suppress.csv'} ({plural(stats['suppressed'], 'address', 'addresses')} "
          f"never to email again)")
    print(f"Also: {out / 'followups.csv'}, {out / 'new_leads.csv'}")


def _bill(args) -> None:
    c = _campaign(args.campaign)
    leads = _leads(args, c, quiet=True)
    require(args.meetings)
    rows, totals = bill(read_meetings(args.meetings), leads, c)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "invoice.csv", rows, INVOICE_COLUMNS)
    for r in rows:
        print(f"  {r['date']} {r['name']} ({r['company']}): {r['billable']} - {r['reason']}")
    print(f"Invoice: platform fee ${totals['platform_fee']:,.0f} + {totals['billable_meetings']} meetings x "
          f"${totals['per_meeting']:,.0f} = ${totals['invoice_total']:,.0f}"
          + (f"; {totals['to_confirm']} to confirm first" if totals["to_confirm"] else ""))
    print(f"File: {out / 'invoice.csv'}")


def _sample(args) -> None:
    from .sample import make_sample

    out = Path(args.out)
    truth = make_sample(out)
    print(f"Wrote {out / 'campaign.toml'}, {out / 'leads.csv'} ({plural(len(truth['leads']), 'lead')}), "
          f"{plural(len(truth['replies']), 'reply', 'replies')} in {out / 'replies'}, {out / 'meetings.csv'} "
          f"and the answer key {out / 'truth.json'}")


def _score(args) -> None:
    from .sample import format_score, score

    require(args.run, args.truth)
    run = Path(args.run)

    def load(name):
        p = run / name
        return json.loads(p.read_text()) if p.exists() else None

    print(format_score(score(load("sequences.json"), load("replies.json"), json.loads(Path(args.truth).read_text()))))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="outreach", description="Cold-email sequences, reply triage and meeting billing.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sm = sub.add_parser("sample", help="write the synthetic campaign (fictional companies) with its answer key")
    sm.add_argument("--out", default="examples/outreach", help="folder (default examples/outreach)")

    def base(sp, leads=True):
        sp.add_argument("--campaign", required=True, help="the client's campaign settings (TOML)")
        if leads:
            sp.add_argument("--leads", required=True, help="leads CSV/Excel (leadagent's leads.csv or an Apollo export)")

    ld = sub.add_parser("leads", help="check the leads against the meeting bar (no model calls)")
    base(ld)
    ld.add_argument("--suppress", help="do-not-contact list (CSV with an email column), e.g. an earlier suppress.csv")

    w = sub.add_parser("write", help="write each lead's email sequence")
    base(w)
    w.add_argument("--suppress", help="do-not-contact list (CSV with an email column), e.g. an earlier suppress.csv")
    w.add_argument("--out", default="out/outreach", help="output folder (default out/outreach)")
    w.add_argument("--model", help="model id (default OUTREACH_MODEL, then LEADAGENT_MODEL)")
    w.add_argument("--limit", type=int, help="only the first N leads (for a quick test)")

    rp = sub.add_parser("replies", help="sort replies, draft responses, list opt-outs and follow-ups")
    base(rp)
    rp.add_argument("--replies", required=True, help="folder of replies (.eml or .txt)")
    rp.add_argument("--out", default="out/outreach", help="output folder (default out/outreach)")
    rp.add_argument("--model", help="model id (default OUTREACH_MODEL, then LEADAGENT_MODEL)")

    b = sub.add_parser("bill", help="work out the invoice from the meetings held (no model calls)")
    base(b)
    b.add_argument("--meetings", required=True, help="meetings CSV: date, email, name, title, company, status")
    b.add_argument("--out", default="out/outreach", help="output folder (default out/outreach)")

    sc = sub.add_parser("score", help="compare a run on the synthetic campaign with its answer key")
    sc.add_argument("--run", default="out/outreach", help="the run's output folder (default out/outreach)")
    sc.add_argument("--truth", default="examples/outreach/truth.json", help="answer key from `sample`")

    args = p.parse_args(argv)
    load_dotenv()
    if args.cmd == "sample":
        _sample(args)
    elif args.cmd == "leads":
        _leads_cmd(args)
    elif args.cmd == "write":
        asyncio.run(_write(args))
    elif args.cmd == "replies":
        asyncio.run(_replies(args))
    elif args.cmd == "bill":
        _bill(args)
    else:
        _score(args)


if __name__ == "__main__":
    main()
