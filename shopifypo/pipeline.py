"""PO emails in, checked draft orders out: one cached model request per email, then the checks in code."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from agentkit.inbox import Email
from agentkit.runner import Runner
from agentkit.tables import pick, read_table

from .catalog import Catalog, Customer
from .check import Order, check_po
from .extract import POReading, build_prompt, system_prompt
from .shop import ShopConfig

# Bump when the prompt, schema or checks change, so cached replies from older logic are redone.
CACHE_VERSION = "1"


def read_seen(path: str | Path | None) -> dict[str, str]:
    """POs entered in earlier runs (an earlier po_log.csv), so a resent PO isn't entered twice."""
    if not path or not Path(path).exists():
        return {}
    return {pick(r, "key"): f"{pick(r, 'file')}, {pick(r, 'date')}" for r in read_table(path) if pick(r, "key")}


async def read_all(emails: list[Email], catalog: Catalog, customers: list[Customer], cfg: ShopConfig,
                   runner: Runner, seen: dict[str, str], progress: Callable[[str], None] = print) -> list[Order]:
    system = system_prompt(cfg.name)
    orders = []
    for i, e in enumerate(emails, 1):
        tag = f"[{i}/{len(emails)}] {e.file}"
        got, err = await runner.ask(system, build_prompt(e), POReading, max_tokens=4000)
        if got is None:
            orders.append(Order(email=e, error=err))
            progress(f"{tag}: {err}")
            continue
        o = check_po(got, e, catalog, customers, cfg, seen)
        if o.po_number and o.kind == "purchase_order" and o.key not in seen:
            seen[o.key] = f"{e.file}, {e.day.isoformat() if e.day else 'no date'}"
        orders.append(o)
        holds = sum(1 for f in o.all_flags() if f.hold)
        what = o.kind.replace("_", " ") + (f" {o.po_number}" if o.po_number else "")
        progress(f"{tag}: {what}, {len(o.lines)} lines, " + ("ready" if o.ready else f"{holds} to fix" if holds else
                                                             "nothing to enter"))
    return orders
