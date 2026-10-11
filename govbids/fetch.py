"""Downloads notices from the SAM.gov Opportunities API (free API key) into the CSV that `match` reads."""

from __future__ import annotations

import html
import os
import re
from datetime import date
from pathlib import Path

import httpx

from agentkit.tables import write_csv

SEARCH = "https://api.sam.gov/opportunities/v2/search"
COLUMNS = ["NoticeId", "Title", "Sol#", "Department/Ind.Agency", "PostedDate", "Type", "SetASideCode", "SetASide",
           "ResponseDeadLine", "NaicsCode", "PopCity", "PopState", "Link", "Description"]


def _text(raw: str) -> str:
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", raw or "")
    return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()


def _description(client: httpx.Client, value: str, api_key: str) -> str:
    """The API gives the description as a link; fetch it (with the key) unless it's already text."""
    if not value or not value.startswith("http"):
        return _text(value)
    try:
        r = client.get(value, params={"api_key": api_key})
    except httpx.HTTPError:
        return ""
    if r.status_code != 200:
        return ""
    try:
        return _text(r.json().get("description", ""))
    except ValueError:
        return _text(r.text)


def fetch_notices(api_key: str, posted_from: date, posted_to: date, naics: list[str],
                  client: httpx.Client | None = None, limit: int = 1000) -> list[dict]:
    client = client or httpx.Client(timeout=60.0)
    rows, seen = [], set()
    for code in naics:
        offset = 0
        while True:
            r = client.get(SEARCH, params={"api_key": api_key, "postedFrom": posted_from.strftime("%m/%d/%Y"),
                                           "postedTo": posted_to.strftime("%m/%d/%Y"), "ncode": code,
                                           "limit": limit, "offset": offset})
            if r.status_code != 200:
                raise RuntimeError(f"SAM.gov answered {r.status_code}: {r.text[:200]}")
            data = r.json()
            items = data.get("opportunitiesData") or []
            for o in items:
                if o.get("noticeId") in seen:
                    continue
                seen.add(o.get("noticeId"))
                pop = o.get("placeOfPerformance") or {}
                rows.append({"NoticeId": o.get("noticeId", ""), "Title": o.get("title", ""),
                             "Sol#": o.get("solicitationNumber", ""),
                             "Department/Ind.Agency": o.get("fullParentPathName", ""),
                             "PostedDate": o.get("postedDate", ""), "Type": o.get("type", ""),
                             "SetASideCode": o.get("typeOfSetAside") or "",
                             "SetASide": o.get("typeOfSetAsideDescription") or "",
                             "ResponseDeadLine": o.get("responseDeadLine") or "", "NaicsCode": o.get("naicsCode", ""),
                             "PopCity": ((pop.get("city") or {}).get("name") or ""),
                             "PopState": ((pop.get("state") or {}).get("code") or ""),
                             "Link": o.get("uiLink", ""),
                             "Description": _description(client, o.get("description", ""), api_key)})
            offset += len(items)
            if not items or offset >= int(data.get("totalRecords") or 0):
                break
    return rows


def save(rows: list[dict], path: str | Path) -> None:
    write_csv(path, rows, COLUMNS)


def key_from_env() -> str:
    return os.environ.get("SAM_API_KEY", "")
