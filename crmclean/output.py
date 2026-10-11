"""Writes the proposals a person approves (changes and merges), the review list, and the import files for HubSpot."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from agentkit.tables import Sheet, read_table, write_csv, write_xlsx

from .pipeline import Result

CHANGE_COLUMNS = ["approve", "object", "record_id", "name", "field", "old", "new", "reason", "by", "source"]
MERGE_COLUMNS = ["approve", "object", "keep_id", "keep", "merge_id", "merge", "reason"]
REVIEW_COLUMNS = ["object", "record_id", "name", "issue", "check"]
ISSUE_ORDER = ["invalid_email", "possible_duplicate", "role_address", "no_company", "invalid_phone", "stale", "unbacked",
               "unknown_industry", "long_description", "not_enriched"]
# Internal property names -> the column names HubSpot's importer maps automatically.
LABELS = {"firstname": "First Name", "lastname": "Last Name", "email": "Email", "phone": "Phone Number",
          "mobilephone": "Mobile Phone Number", "jobtitle": "Job Title", "lifecyclestage": "Lifecycle Stage",
          "city": "City", "state": "State/Region", "country": "Country/Region", "name": "Company name",
          "domain": "Company Domain Name", "industry": "Industry", "numberofemployees": "Number of Employees",
          "description": "Description"}


def change_rows(res: Result) -> list[dict]:
    """Rule changes come pre-approved; anything the model filled in waits for a person's "yes"."""
    return [{"approve": "yes" if c.by == "rule" else "", "object": c.kind, "record_id": c.id, "name": c.label,
             "field": c.field, "old": c.old, "new": c.new, "reason": c.reason, "by": c.by, "source": c.source}
            for c in res.changes]


def merge_rows(res: Result) -> list[dict]:
    """Merging can't be undone in HubSpot, so every merge waits for a person's "yes"."""
    return [{"approve": "", "object": m.kind, "keep_id": m.primary, "keep": m.primary_label, "merge_id": m.duplicate,
             "merge": m.duplicate_label, "reason": m.reason} for m in res.merges]


def review_rows(res: Result) -> list[dict]:
    rows = [{"object": f.kind, "record_id": f.id, "name": f.label, "issue": f.code, "check": f.text} for f in res.flags]
    rows += [{"object": "company", "record_id": "", "name": x, "issue": "no_page", "check": "no website text to enrich "
              "from (pass --sites or --fetch)"} for x in res.no_page]
    rows.sort(key=lambda r: ISSUE_ORDER.index(r["issue"]) if r["issue"] in ISSUE_ORDER else len(ISSUE_ORDER))
    return rows


def write_outputs(out: Path, res: Result) -> dict:
    changes, merges, review = change_rows(res), merge_rows(res), review_rows(res)
    write_csv(out / "changes.csv", changes, CHANGE_COLUMNS)
    write_csv(out / "merges.csv", merges, MERGE_COLUMNS)
    write_csv(out / "review.csv", review, REVIEW_COLUMNS)
    write_xlsx(out / "cleanup.xlsx", [
        Sheet("Changes", [c.replace("_", " ").capitalize() for c in CHANGE_COLUMNS],
              [[r[c] for c in CHANGE_COLUMNS] for r in changes], {i for i, r in enumerate(changes) if not r["approve"]},
              {3: 24, 6: 28, 7: 32, 8: 36, 10: 50}),
        Sheet("Merges", [c.replace("_", " ").capitalize() for c in MERGE_COLUMNS],
              [[r[c] for c in MERGE_COLUMNS] for r in merges], set(range(len(merges))), {4: 28, 6: 28, 7: 36}),
        Sheet("Review", [c.replace("_", " ").capitalize() for c in REVIEW_COLUMNS],
              [[r[c] for c in REVIEW_COLUMNS] for r in review], set(), {3: 26, 4: 18, 5: 80}),
    ])
    (out / "results.json").write_text(json.dumps({"changes": [asdict(c) for c in res.changes],
                                                  "merges": [asdict(m) for m in res.merges],
                                                  "flags": [asdict(f) for f in res.flags], "no_page": res.no_page},
                                                 indent=1))
    return {"changes": len(changes), "merges": len(merges), "review": len(review)}


# --- after review: what to send to HubSpot ------------------------------------------------------------------------

def approved(run: Path) -> tuple[list[dict], list[dict]]:
    """The rows a person left (or marked) "yes" in changes.csv and merges.csv."""
    ch = [r for r in read_table(run / "changes.csv") if r.get("approve", "").strip().lower() in ("yes", "y", "x")]
    mg = [r for r in read_table(run / "merges.csv") if r.get("approve", "").strip().lower() in ("yes", "y", "x")]
    return ch, mg


def updates(changes: list[dict]) -> tuple[dict[str, dict[str, dict]], list[tuple[str, str]]]:
    """({object: {record id: {property: value}}}, [(contact id, company id)] associations)."""
    props: dict[str, dict[str, dict]] = {"contact": {}, "company": {}}
    links = []
    for r in changes:
        if r["field"] == "associatedcompanyid":
            links.append((r["record id"], r["new"]))
        else:
            props[r["object"]].setdefault(r["record id"], {})[r["field"]] = r["new"]
    return props, links


def write_imports(out: Path, changes: list[dict]) -> list[Path]:
    """HubSpot import files: Record ID plus the changed columns, one file per object."""
    props, links = updates(changes)
    files = []
    for kind, rows in props.items():
        if not rows:
            continue
        fields = sorted({f for p in rows.values() for f in p})
        path = out / f"import_{'companies' if kind == 'company' else 'contacts'}.csv"
        write_csv(path, [{"Record ID": rid, **{LABELS.get(f, f): v for f, v in p.items()}} for rid, p in rows.items()],
                  ["Record ID"] + [LABELS.get(f, f) for f in fields])
        files.append(path)
    if links:
        path = out / "associations.csv"
        write_csv(path, [{"contact_id": a, "company_id": b} for a, b in links], ["contact_id", "company_id"])
        files.append(path)
    return files
