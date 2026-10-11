"""Applies approved changes through the HubSpot API (private app token). Dry run unless asked."""

from __future__ import annotations

import os
import re

import httpx

BASE = "https://api.hubapi.com"
OBJECTS = {"contact": "contacts", "company": "companies"}


class HubSpotError(RuntimeError):
    pass


def enum_value(label: str) -> str:
    """HubSpot's internal value for an industry label: 'Food & Beverages' -> 'FOOD_BEVERAGES'."""
    return re.sub(r"[^A-Z0-9]+", "_", label.upper()).strip("_")


class HubSpot:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=30.0)
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _send(self, method: str, path: str, body: dict | None = None) -> dict:
        r = self.client.request(method, BASE + path, json=body, headers=self.headers)
        if r.status_code >= 300:
            raise HubSpotError(f"HubSpot answered {r.status_code} to {path}: {r.text[:200]}")
        return r.json() if r.content else {}

    def batch_update(self, kind: str, rows: dict[str, dict]) -> int:
        items = [{"id": rid, "properties": {k: enum_value(v) if k == "industry" else v for k, v in p.items()}}
                 for rid, p in rows.items()]
        for i in range(0, len(items), 100):
            self._send("POST", f"/crm/v3/objects/{OBJECTS[kind]}/batch/update", {"inputs": items[i:i + 100]})
        return len(items)

    def associate(self, contact_id: str, company_id: str) -> None:
        self._send("PUT", f"/crm/v4/objects/contact/{contact_id}/associations/default/company/{company_id}")

    def merge(self, kind: str, keep_id: str, merge_id: str) -> None:
        self._send("POST", f"/crm/v3/objects/{OBJECTS[kind]}/merge",
                   {"primaryObjectId": keep_id, "objectIdToMerge": merge_id})


def apply(hs: HubSpot, props: dict[str, dict[str, dict]], links: list[tuple[str, str]], merges: list[dict]) -> dict:
    """Merges first (so updates land on the record that stays), then property updates, then associations."""
    done = {"merges": 0, "contacts": 0, "companies": 0, "associations": 0}
    for m in merges:
        hs.merge(m["object"], m["keep id"], m["merge id"])
        done["merges"] += 1
    gone = {m["merge id"] for m in merges}
    for kind, rows in props.items():
        rows = {rid: p for rid, p in rows.items() if rid not in gone}
        if rows:
            done[OBJECTS[kind]] += hs.batch_update(kind, rows)
    for contact, company in links:
        if contact not in gone and company not in gone:
            hs.associate(contact, company)
            done["associations"] += 1
    return done


def token_from_env() -> str:
    return os.environ.get("HUBSPOT_ACCESS_TOKEN", "")
