"""A synthetic HubSpot portal (fictional companies) with planted problems, saved homepages and the answer key."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

TODAY = "2026-10-12"

CRM_TOML = """# Cleanup settings for one HubSpot portal (fictional example).
[crm]
portal = "Pinecrest Marketing"
stale_months = 18          # leads with no activity for this long are flagged
state_format = "name"      # "name" (California) or "code" (CA)
country_name = "United States"
"""

COMPANY_HEADER = ["Record ID", "Company name", "Company Domain Name", "Industry", "Number of Employees", "City",
                  "State/Region", "Country/Region", "Phone Number", "Description", "Last Activity Date", "Create Date"]
COMPANIES = [
    ["1001", "Northbeam Analytics", "northbeam.example", "", "", "", "", "USA", "", "", "2026-09-15", "2024-03-02"],
    ["1002", "ACME ANALYTICS INC", "acme-analytics.example", "Computer Software", "120", "Austin", "TX",
     "United States", "(512) 555-0182", "Analytics software for retailers.", "2026-08-01", "2023-01-10"],
    ["1003", "Acme Analytics", "https://www.acme-analytics.example/", "", "", "", "", "", "", "", "2025-02-11",
     "2025-02-11"],
    ["1004", "BRIGHTWAVE SOLAR LLC", "brightwave-solar.example", "Renewables & Environment", "35", "Fresno",
     "California", "United States", "559.555.0143", "Commercial solar installation.", "2026-07-20", "2022-05-05"],
    ["1005", "Tidewater Cold Storage", "tidewater-cold.example", "", "", "", "", "United States", "", "", "2026-06-01",
     "2024-09-09"],
    ["1006", "Copperline Dental Labs", "copperline-dental.example", "", "18", "", "", "United States", "", "",
     "2026-05-12", "2024-11-11"],
    ["1007", "Fernhill Bakery Supply", "fernhill-bakery.example", "Wholesale", "", "", "", "", "", "", "2026-09-30",
     "2025-06-06"],
    ["1008", "Lakeshore Physical Therapy", "", "Hospital & Health Care", "22", "Erie", "PA", "United States", "", "",
     "2026-03-10", "2021-08-08"],
    ["1009", "Lakeshore Physical Therapy", "lakeshore-pt.example", "", "", "", "", "", "", "", "2026-04-04",
     "2025-10-01"],
    ["1010", "Granite Peak Consulting", "granitepeak.example", "Management Consulting", "9", "Boulder", "CO",
     "United States", "+44 20 7946 0958", "Operations consulting for manufacturers.", "2026-09-01", "2020-02-02"],
]
CONTACT_HEADER = ["Record ID", "First Name", "Last Name", "Email", "Phone Number", "Job Title", "Lifecycle Stage",
                  "Associated Company ID", "Last Activity Date", "Create Date"]
CONTACTS = [
    ["201", "Jane", "Doe", "Jane.Doe@northbeam.example", "(614) 555-0101", "CEO", "customer", "1001", "2026-09-14",
     "2024-03-02"],
    ["202", "Jane", "Doe", "jane.doe@northbeam.example", "", "Chief Executive Officer", "lead", "", "2025-01-20",
     "2025-01-20"],
    ["203", "MARK", "RIVERA", "mark.rivera@acme-analytics.example", "512-555-0199", "VP Sales", "opportunity", "1002",
     "2026-08-20", "2023-01-11"],
    ["204", "priya", "shah", "priya.shah@acme-analytics.example ", "", "Analyst", "lead", "", "2026-07-07",
     "2026-07-07"],
    ["205", "Chris", "Lee", "chris.lee@brightwave-solar.example", "559-555-0110", "Ops Manager", "lead", "1004",
     "2026-07-01", "2025-03-03"],
    ["206", "Chris", "Lee", "clee@brightwave-solar.example", "", "Operations Manager", "lead", "1004", "2026-06-15",
     "2026-06-15"],
    ["207", "Info", "", "info@tidewater-cold.example", "", "", "subscriber", "1005", "2026-05-05", "2025-05-05"],
    ["208", "Sam", "Patel", "sam.patel@gmail.com", "555-0199", "Owner", "lead", "", "2026-09-10", "2026-09-10"],
    ["209", "Dana", "Whitfield", "dana@@copperline-dental.example", "", "Lab Manager", "lead", "1006", "2026-03-03",
     "2025-03-03"],
    ["210", "Tom", "Becker", "tom.becker@fernhill-bakery.example", "+1 (503) 555-0177", "Purchasing", "lead", "",
     "2024-02-02", "2023-12-12"],
    ["211", "Ana", "Lopez", "ana.lopez@granitepeak.example", "+44 20 7946 0958", "Partner", "customer", "1010",
     "2026-09-02", "2020-02-03"],
    ["212", "Lee", "Wong", "lee.wong@unknownco.example", "", "CTO", "lead", "", "2026-08-08", "2026-08-08"],
]

PAGES = {
    "northbeam.example": ("Northbeam Analytics", [
        "Northbeam Analytics builds forecasting dashboards for regional grocery chains.",
        "Our team of 42 data scientists and engineers works from our office in Columbus, Ohio.",
        "Book a demo to see next week's demand, store by store."]),
    "tidewater-cold.example": ("Tidewater Cold Storage", [
        "Tidewater Cold Storage runs temperature-controlled warehouses for seafood and produce shippers on the Gulf "
        "Coast.", "Headquarters: Mobile, Alabama.", "Ask about blast freezing and cross-docking."]),
    "copperline-dental.example": ("Copperline Dental Labs", [
        "Copperline Dental Labs makes crowns, bridges and implant restorations for dental practices across the "
        "Midwest.", "Visit our lab in Madison, Wisconsin.", "Digital impressions accepted from all major scanners."]),
    "fernhill-bakery.example": ("Fernhill Bakery Supply", [
        "Fernhill Bakery Supply delivers flour, sugar and baking equipment to independent bakeries in Oregon and "
        "Washington.", "Family owned since 1987, based in Salem, Oregon.", "Order by 2pm for next-day delivery."]),
}


def _v(value, quote) -> dict:
    return {"value": value, "quote": quote}


# What a perfect model answers for each company it is asked to fill in.
ENRICH = {
    "Northbeam Analytics": {
        "industry": _v("Computer Software", PAGES["northbeam.example"][1][0]),
        "description": _v("Builds forecasting dashboards for regional grocery chains.", PAGES["northbeam.example"][1][0]),
        "employees": _v("42", PAGES["northbeam.example"][1][1]),
        "city": _v("Columbus", PAGES["northbeam.example"][1][1]), "state": _v("Ohio", PAGES["northbeam.example"][1][1])},
    "Tidewater Cold Storage": {
        "industry": _v("Warehousing", PAGES["tidewater-cold.example"][1][0]),
        "description": _v("Runs temperature-controlled warehouses for seafood and produce shippers on the Gulf Coast.",
                          PAGES["tidewater-cold.example"][1][0]),
        "employees": _v("", ""), "city": _v("Mobile", "Headquarters: Mobile, Alabama."),
        "state": _v("Alabama", "Headquarters: Mobile, Alabama.")},
    "Copperline Dental Labs": {
        "industry": _v("Medical Devices", PAGES["copperline-dental.example"][1][0]),
        "description": _v("Makes crowns, bridges and implant restorations for dental practices across the Midwest.",
                          PAGES["copperline-dental.example"][1][0]),
        "employees": _v("", ""), "city": _v("Madison", "Visit our lab in Madison, Wisconsin."),
        "state": _v("Wisconsin", "Visit our lab in Madison, Wisconsin.")},
    "Fernhill Bakery Supply": {
        "industry": _v("", ""),
        "description": _v("Delivers flour, sugar and baking equipment to independent bakeries in Oregon and Washington.",
                          PAGES["fernhill-bakery.example"][1][0]),
        "employees": _v("", ""), "city": _v("Salem", "Family owned since 1987, based in Salem, Oregon."),
        "state": _v("Oregon", "Family owned since 1987, based in Salem, Oregon.")},
}

EXPECT_CHANGES = [  # (object, record id, field, new value)
    ("company", "1001", "country", "United States"), ("company", "1002", "name", "Acme Analytics Inc"),
    ("company", "1002", "state", "Texas"), ("company", "1002", "phone", "+1 512-555-0182"),
    ("company", "1004", "name", "Brightwave Solar LLC"), ("company", "1004", "phone", "+1 559-555-0143"),
    ("company", "1008", "state", "Pennsylvania"), ("company", "1010", "state", "Colorado"),
    ("contact", "201", "email", "jane.doe@northbeam.example"), ("contact", "201", "phone", "+1 614-555-0101"),
    ("contact", "203", "firstname", "Mark"), ("contact", "203", "lastname", "Rivera"),
    ("contact", "203", "phone", "+1 512-555-0199"), ("contact", "204", "email", "priya.shah@acme-analytics.example"),
    ("contact", "204", "firstname", "Priya"), ("contact", "204", "lastname", "Shah"),
    ("contact", "204", "associatedcompanyid", "1002"), ("contact", "205", "phone", "+1 559-555-0110"),
    ("contact", "210", "associatedcompanyid", "1007"), ("contact", "210", "phone", "+1 503-555-0177"),
    # filled in from the companies' own websites
    ("company", "1001", "industry", "Computer Software"),
    ("company", "1001", "description", "Builds forecasting dashboards for regional grocery chains."),
    ("company", "1001", "numberofemployees", "42"), ("company", "1001", "city", "Columbus"),
    ("company", "1001", "state", "Ohio"), ("company", "1005", "industry", "Warehousing"),
    ("company", "1005", "description",
     "Runs temperature-controlled warehouses for seafood and produce shippers on the Gulf Coast."),
    ("company", "1005", "city", "Mobile"), ("company", "1005", "state", "Alabama"),
    ("company", "1006", "industry", "Medical Devices"),
    ("company", "1006", "description",
     "Makes crowns, bridges and implant restorations for dental practices across the Midwest."),
    ("company", "1006", "city", "Madison"), ("company", "1006", "state", "Wisconsin"),
    ("company", "1007", "description",
     "Delivers flour, sugar and baking equipment to independent bakeries in Oregon and Washington."),
    ("company", "1007", "city", "Salem"), ("company", "1007", "state", "Oregon"),
]
EXPECT_MERGES = [("company", "1002", "1003"), ("contact", "201", "202")]
EXPECT_FLAGS = [("company", "1008", "possible_duplicate"), ("company", "1009", "possible_duplicate"),
                ("contact", "205", "possible_duplicate"), ("contact", "206", "possible_duplicate"),
                ("contact", "207", "role_address"), ("contact", "208", "invalid_phone"),
                ("contact", "209", "invalid_email"), ("contact", "210", "stale"), ("contact", "212", "no_company")]


def _csv(header: list[str], rows: list[list[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def _html(title: str, paras: list[str]) -> str:
    body = "\n".join(f"    <p>{p}</p>" for p in paras)
    return (f"<!doctype html>\n<html>\n  <head><title>{title}</title></head>\n  <body>\n    <h1>{title}</h1>\n"
            f"{body}\n  </body>\n</html>\n")


def make_sample(out: Path) -> dict:
    """Writes companies.csv, contacts.csv, sites/, crm.toml and truth.json; returns the answer key."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "companies.csv").write_text(_csv(COMPANY_HEADER, COMPANIES), encoding="utf-8")
    (out / "contacts.csv").write_text(_csv(CONTACT_HEADER, CONTACTS), encoding="utf-8")
    (out / "sites").mkdir(exist_ok=True)
    for domain, (title, paras) in PAGES.items():
        (out / "sites" / f"{domain}.html").write_text(_html(title, paras), encoding="utf-8")
    (out / "crm.toml").write_text(CRM_TOML, encoding="utf-8")
    truth = {"note": "Answer key for the synthetic HubSpot portal made by `python -m crmclean sample`. Fictional "
                     f"companies. Run with --today {TODAY}. `enrich` is what a perfect model returns.",
             "today": TODAY, "enrich": ENRICH,
             "changes": [list(c) for c in EXPECT_CHANGES], "merges": [list(m) for m in EXPECT_MERGES],
             "flags": [list(f) for f in EXPECT_FLAGS]}
    (out / "truth.json").write_text(json.dumps(truth, indent=1) + "\n", encoding="utf-8")
    return truth


# --- scoring a run against the answer key -------------------------------------------------------------------------

def score(results: dict, truth: dict) -> dict:
    got = {(c["kind"], c["id"], c["field"], c["new"]) for c in results["changes"]}
    want = {tuple(c) for c in truth["changes"]}
    gm = {(m["kind"], m["primary"], m["duplicate"]) for m in results["merges"]}
    wm = {tuple(m) for m in truth["merges"]}
    gf = {(f["kind"], f["id"], f["code"]) for f in results["flags"]}
    wf = {tuple(f) for f in truth["flags"]}
    return {"changes_found": len(got & want), "changes_expected": len(want),
            "missed_changes": sorted(" ".join(x) for x in want - got),
            "extra_changes": sorted(" ".join(x) for x in got - want),
            "merges_found": len(gm & wm), "merges_expected": len(wm), "extra_merges": sorted(" ".join(x) for x in gm - wm),
            "flags_found": len(gf & wf), "flags_expected": len(wf), "missed_flags": sorted(" ".join(x) for x in wf - gf),
            "extra_flags": sorted(" ".join(x) for x in gf - wf)}


def _pct(a: int, b: int) -> str:
    return f"{a} of {b}" + (f" ({100 * a / b:.0f}%)" if b else "")


def format_score(s: dict) -> str:
    lines = [f"Expected changes proposed: {_pct(s['changes_found'], s['changes_expected'])}; other changes: "
             f"{len(s['extra_changes'])}"]
    lines += [f"  missed: {m}" for m in s["missed_changes"]] + [f"  other: {m}" for m in s["extra_changes"]]
    lines.append(f"Duplicates to merge found: {_pct(s['merges_found'], s['merges_expected'])}; other merges: "
                 f"{len(s['extra_merges'])}")
    lines += [f"  other: {m}" for m in s["extra_merges"]]
    lines.append(f"Problems flagged for review: {_pct(s['flags_found'], s['flags_expected'])}; other flags: "
                 f"{len(s['extra_flags'])}")
    lines += [f"  missed: {m}" for m in s["missed_flags"]] + [f"  other: {m}" for m in s["extra_flags"]]
    return "\n".join(lines)
