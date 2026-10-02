"""Where the Actor runs: on Apify through the Apify SDK, or on your machine with a simulated price."""

from __future__ import annotations

import csv
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import AsyncIterator

from .actor import EVENT, InputError, SetupError, actor_settings, execute, summary

UNLIMITED = 10**9
RESULT_COLUMNS = ["website", "companyName", "qualified", "score", "whyNow", "whyNowSource", "openingLine",
                  "openingLineStatus", "summary", "signals", "criteria"]


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_items(path: Path) -> list[dict]:
    """Items from a .json (list), .jsonl or .csv file: the local stand-in for another Actor's dataset."""
    if path.suffix.lower() == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    if path.suffix.lower() == ".jsonl":
        return read_jsonl(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("items", [])


def flat_row(item: dict) -> dict:
    return {
        **{k: item.get(k, "") for k in RESULT_COLUMNS},
        "signals": " | ".join(f"{s['type']}: {s['evidence']}" + (f" [{s['date']}]" if s.get("date") else "")
                              + (" (old)" if s.get("old") else "") + f" ({s['sourceUrl']})" for s in item["signals"]),
        "criteria": " | ".join(f"{c['name']}: {c['status']}" + (f" ({c['evidence']})" if c["evidence"] else "")
                               for c in item["criteria"]),
    }


class LocalPlatform:
    """Your machine: input from a dict, rows to dataset.jsonl and results.csv, and an optional simulated price."""

    paid = False  # simulated charges are never real money, so free models may be used for testing

    def __init__(self, data: dict, out_dir: Path, *, price: float | None = None, max_charge: float | None = None,
                 resume: bool = False):
        self.data = data
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.dataset = self.out / "dataset.jsonl"
        if not resume and self.dataset.exists():
            self.dataset.unlink()
        self.price = Decimal(str(price)) if price is not None else None
        self.max_charge = Decimal(str(max_charge)) if max_charge is not None else None
        self.charged = len(read_jsonl(self.dataset))  # a resumed run keeps what it already charged
        self.statuses: list[str] = []

    async def get_input(self) -> dict:
        return self.data

    async def read_dataset(self, dataset_id: str) -> AsyncIterator[dict]:
        path = Path(dataset_id)
        if not path.is_file():
            raise FileNotFoundError(f"{dataset_id} not found (locally, datasetId is a .json, .jsonl or .csv file)")
        for item in load_items(path):
            yield item

    async def done_items(self) -> list[dict]:
        return read_jsonl(self.dataset)

    def pricing_problem(self) -> str:
        return ""

    def chargeable(self) -> int | None:
        if self.price is None or self.max_charge is None or self.price <= 0:
            return None
        return max(0, math.floor((self.max_charge - self.charged * self.price) / self.price))

    async def push(self, item: dict) -> bool:
        with open(self.dataset, "a", encoding="utf-8") as f:
            f.write(json.dumps(item) + "\n")
        self.charged += 1
        return True

    async def set_status(self, message: str) -> None:
        self.statuses.append(message)

    async def save_summary(self, data: dict) -> None:
        (self.out / "run_summary.json").write_text(json.dumps(data, indent=2))
        rows = sorted(read_jsonl(self.dataset), key=lambda i: (not i["qualified"], -i["score"]))
        with open(self.out / "results.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS)
            writer.writeheader()
            writer.writerows(flat_row(i) for i in rows)


class ApifyPlatform:
    """The Apify platform, through the Apify SDK's Actor object."""

    def __init__(self, actor):
        self.actor = actor
        self.charging = actor.get_charging_manager()
        pricing = self.charging.get_pricing_info()
        self.paid = pricing.is_pay_per_event
        self.prices = pricing.per_event_prices

    async def get_input(self) -> dict:
        return await self.actor.get_input() or {}

    async def read_dataset(self, dataset_id: str) -> AsyncIterator[dict]:
        dataset = await self.actor.open_dataset(id=dataset_id)
        async for item in dataset.iterate_items():
            yield dict(item)

    async def done_items(self) -> list[dict]:
        dataset = await self.actor.open_dataset()
        return [dict(item) async for item in dataset.iterate_items()]

    def pricing_problem(self) -> str:
        if self.paid and not self.prices.get(EVENT):
            return (f"Pay-per-event pricing has no priced '{EVENT}' event, so results would be free. "
                    "Add it in Apify Console under Publication > Monetization.")
        return ""

    def chargeable(self) -> int | None:
        if not self.paid:
            return None
        # the same sum push_data() uses, so it also counts a price on Apify's own per-dataset-item event
        n = self.charging.compute_push_data_limit(items_count=UNLIMITED, event_name=EVENT, is_default_dataset=True)
        return None if n >= UNLIMITED else n

    async def push(self, item: dict) -> bool:
        if not self.paid:
            await self.actor.push_data(item)
            return True
        # stores the row and charges for it together; stores nothing if the user's maximum charge can't cover it
        result = await self.actor.push_data(item, charged_event_name=EVENT)
        return result.charged_count > 0

    async def set_status(self, message: str) -> None:
        await self.actor.set_status_message(message)

    async def save_summary(self, data: dict) -> None:
        await self.actor.set_value("OUTPUT", data)


async def run_on_apify() -> None:
    """Entry point on the Apify platform (see .actor/Dockerfile)."""
    from apify import Actor

    async with Actor:
        platform = ApifyPlatform(Actor)
        try:
            outcome, job, _ = await execute(platform, actor_settings(), progress=Actor.log.info, show_errors=False)
        except (InputError, SetupError) as e:
            await Actor.fail(status_message=str(e))
            return
        await platform.save_summary(summary(outcome, job))
        await Actor.set_status_message(outcome.message(), is_terminal=True)
