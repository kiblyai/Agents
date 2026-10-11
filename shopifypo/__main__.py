"""Command line: `python -m shopifypo sample | inbox | run | push | score`."""

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

from .catalog import Catalog, read_catalog, read_customers, read_prices
from .output import summary, write_outputs
from .pipeline import CACHE_VERSION, read_all, read_seen
from .shop import load_shop


def _shop(path):
    require(path)
    try:
        return load_shop(path)
    except ValueError as e:
        sys.exit(str(e))


def _inbox(folder: str):
    require(folder)
    emails, skipped = read_inbox(folder)
    print(f"Inbox: {plural(len(emails), 'email')} in {folder}")
    for e in emails:
        sent = e.sent.strftime("%a %Y-%m-%d %H:%M") if e.sent else "no date"
        extra = f"; attachments: {', '.join(a.name for a in e.attachments)}" if e.attachments else ""
        extra += f"; unreadable: {', '.join(e.skipped)}" if e.skipped else ""
        print(f"  {e.file}: {sent}, {e.sender or 'no sender'}, \"{e.subject}\"{extra}")
    for s in skipped:
        print(f"  skipped {s}")
    return emails


def _catalog(args):
    require(args.catalog, args.prices, args.customers)
    try:
        variants, problems = read_catalog(args.catalog)
        prices = read_prices(args.prices)
        customers = read_customers(args.customers)
    except ValueError as e:
        sys.exit(str(e))
    print(f"Catalog: {plural(len(variants), 'variant')}; price list: {plural(len(prices), 'row')}; "
          f"customers: {len(customers)}" + (f" ({plural(len(problems), 'catalog row')} to note)" if problems else ""))
    if not args.customers:
        print("  No --customers file: every buyer will be flagged as new.")
    return Catalog(variants, prices), customers, problems


def _inbox_cmd(args) -> None:
    emails = _inbox(args.emails)
    print(f"A run needs {plural(len(emails), 'model request')} (free models allow 50 a day, 1,000 after $10 of credit).")


async def _run(args) -> None:
    cfg = _shop(args.shop) if args.shop else load_shop(None)
    catalog, customers, problems = _catalog(args)
    emails = _inbox(args.emails)
    if not emails:
        sys.exit("No .eml or .txt emails in the folder.")
    if args.limit:
        emails = emails[:args.limit]
    s = Settings.from_env("SHOPIFYPO", "shopifypo")
    if args.model:
        s.model = args.model
    check_key(s)
    if is_free_model(s.model):
        print(free_model_warning("SHOPIFYPO", "a merchant's real orders or customer data"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Model {s.model}; reading {plural(len(emails), 'email')}")
    llm = LLM.from_settings(s)
    runner = Runner(llm, out / "cache", CACHE_VERSION, hint="set SHOPIFYPO_FALLBACK_MODELS")
    seen = read_seen(args.seen)
    orders = await read_all(emails, catalog, customers, cfg, runner, seen)
    stats = write_outputs(out, orders, cfg.tags, seen)
    summ = summary(orders, llm.usage, runner, len(catalog.variants), problems)
    (out / "run_summary.json").write_text(json.dumps(summ, indent=2))
    print(f"\nDone: {plural(summ['purchase_orders'], 'purchase order')}, {summ['ready']} ready for Shopify, "
          f"{summ['to_fix']} to fix, {summ['not_po']} not a PO; {plural(summ['llm_requests'], 'model request')}; "
          f"${summ['cost_usd']:.4f}")
    if runner.stopped_reason:
        print("Stopped early: " + runner.stopped_reason)
    print(f"Check first: {out / 'review.csv'} ({plural(stats['review_items'], 'item')}); all orders and lines in "
          f"{out / 'orders.xlsx'}")
    print(f"Ready drafts: {out / 'draft_orders'}/ (send them with `push`); keep {out / 'po_log.csv'} for --seen next time")


def _push(args) -> None:
    from .push import Shopify, ShopifyError, push, token_from_env

    cfg = _shop(args.shop) if args.shop else load_shop(None)
    require(args.run)
    data = json.loads((Path(args.run) / "orders.json").read_text())
    drafts = data["drafts"]
    if not drafts:
        sys.exit("No ready orders in this run.")
    domain = args.shop_domain or cfg.shop_domain
    token = token_from_env()
    if not domain or not token:
        sys.exit("Set the store with --shop-domain (or shop_domain in shop.toml) and SHOPIFY_ACCESS_TOKEN in your "
                 "environment or .env (a custom app's Admin API token with read_products and write_draft_orders).")
    shop = Shopify(domain, token, cfg.api_version)
    try:
        plans = push(drafts, shop, args.yes)
    except ShopifyError as e:
        sys.exit(str(e))
    rows = []
    for p in plans:
        state = p.created or ("would create" if not args.yes and not p.problems else "skipped")
        print(f"  PO {p.po_number} ({p.file}): {state}" + (f" - {'; '.join(p.problems)}" if p.problems else ""))
        rows.append({"po_number": p.po_number, "file": p.file, "draft": p.created, "status": state,
                     "expected_subtotal": p.expected_subtotal, "shopify_subtotal": p.shopify_subtotal,
                     "problems": "; ".join(p.problems)})
    write_csv(Path(args.run) / "pushed.csv", rows, list(rows[0]))
    if not args.yes:
        print("Dry run: nothing was created. Add --yes to create these draft orders in Shopify.")


def _sample(args) -> None:
    from .sample import make_sample

    out = Path(args.out)
    truth = make_sample(out)
    print(f"Wrote {plural(len(truth['emails']), 'email')} to {out / 'inbox'}, plus catalog.csv, prices.csv, "
          f"customers.csv, shop.toml and the answer key {out / 'truth.json'}")


def _score(args) -> None:
    from .sample import format_score, score

    require(args.run, args.truth)
    results = json.loads((Path(args.run) / "orders.json").read_text())
    print(format_score(score(results, json.loads(Path(args.truth).read_text()))))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="shopifypo", description="Turn emailed wholesale POs into Shopify draft orders.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sm = sub.add_parser("sample", help="write the synthetic wholesale inbox (fictional companies) with its answer key")
    sm.add_argument("--out", default="examples/shopifypo", help="folder (default examples/shopifypo)")

    ib = sub.add_parser("inbox", help="list the emails found and what a run needs (no model calls)")
    ib.add_argument("--emails", required=True, help="folder of .eml or .txt emails")

    r = sub.add_parser("run", help="read the POs, match them to the catalog and check them")
    r.add_argument("--emails", required=True, help="folder of .eml or .txt emails")
    r.add_argument("--catalog", required=True, help="Shopify product export CSV (Products > Export)")
    r.add_argument("--prices", help="wholesale price list CSV: SKU, Wholesale Price, Case Pack, Min Qty, Customer")
    r.add_argument("--customers", help="Shopify customer export CSV (Customers > Export)")
    r.add_argument("--shop", help="shop settings (TOML); defaults otherwise")
    r.add_argument("--seen", help="po_log.csv from an earlier run, to catch POs sent twice")
    r.add_argument("--out", default="out/shopifypo", help="output folder (default out/shopifypo)")
    r.add_argument("--model", help="model id (default SHOPIFYPO_MODEL, then LEADAGENT_MODEL)")
    r.add_argument("--limit", type=int, help="only the first N emails (for a quick test)")

    pu = sub.add_parser("push", help="create the ready orders as Shopify draft orders (dry run unless --yes)")
    pu.add_argument("--run", default="out/shopifypo", help="the run's output folder (default out/shopifypo)")
    pu.add_argument("--shop", help="shop settings (TOML), for shop_domain and api_version")
    pu.add_argument("--shop-domain", help="your-store.myshopify.com")
    pu.add_argument("--yes", action="store_true", help="really create the draft orders")

    sc = sub.add_parser("score", help="compare a run on the synthetic inbox with its answer key")
    sc.add_argument("--run", default="out/shopifypo", help="the run's output folder (default out/shopifypo)")
    sc.add_argument("--truth", default="examples/shopifypo/truth.json", help="answer key from `sample`")

    args = p.parse_args(argv)
    load_dotenv()
    if args.cmd == "sample":
        _sample(args)
    elif args.cmd == "inbox":
        _inbox_cmd(args)
    elif args.cmd == "run":
        asyncio.run(_run(args))
    elif args.cmd == "push":
        _push(args)
    else:
        _score(args)


if __name__ == "__main__":
    main()
