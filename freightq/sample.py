"""A synthetic brokerage inbox (fictional companies) with lane history and an answer key, and a scorer for runs on it.

Everything here is made up: the companies, people, loads and rates. Addresses use the reserved .example domain.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from agentkit.pdfgen import text_pdf, wrap_page

from .extract import city_key

BROKER_EMAIL = "quotes@cohofreight.example"
FOOTER = "Synthetic example: fictional companies, not real shipment data."
CENTRAL, EASTERN, PACIFIC, MOUNTAIN = (timezone(timedelta(hours=h)) for h in (-5, -4, -7, -7))

BROKER_TOML = """# Settings for the fictional brokerage in the example. Copy this file and change it for a real one.
[broker]
name = "Coho Freight Brokerage"
signature = \"\"\"Alex Morgan
Coho Freight Brokerage
quotes@cohofreight.example\"\"\"
target_margin = 0.15      # share of the customer's linehaul rate
min_margin = 150          # dollars per load, at least
round_to = 25             # round customer rates up to this
quote_valid_days = 3
lookback_days = 120       # older lane history is used only when nothing newer exists
max_comparables = 5
required = ["origin", "destination", "equipment", "pickup_date", "weight"]
small_shipment_lbs = 10000
small_shipment_pallets = 6

[accessorials]            # added to the customer's rate
extra_stop = 100
tarps = 125
hazmat = 200
team = 600
liftgate = 75

[max_weight_lbs]          # flag heavier loads
van = 45000
reefer = 43500
flatbed = 48000
stepdeck = 48000
"""

LANE_HEADER = ["date", "customer", "origin_city", "origin_state", "origin_zip", "dest_city", "dest_state", "dest_zip",
               "equipment", "miles", "carrier_pay", "customer_rate"]
DAL, ATL = ("Dallas", "TX", "75207"), ("Atlanta", "GA", "30318")
FRE, DEN = ("Fresno", "CA", "93725"), ("Denver", "CO", "80216")
BHM, HOU_N = ("Birmingham", "AL", "35203"), ("Houston", "TX", "77029")
MEM, CHI = ("Memphis", "TN", "38118"), ("Chicago", "IL", "60632")
GER, OBZ, STL = ("Germantown", "TN", "38138"), ("Obetz", "OH", "43207"), ("St. Louis", "MO", "63147")
JOL, NSH, DET = ("Joliet", "IL", "60436"), ("Nashville", "TN", "37210"), ("Detroit", "MI", "48210")
SAV, CLT = ("Savannah", "GA", "31408"), ("Charlotte", "NC", "28206")
SAL, CHD, PHX = ("Salinas", "CA", "93901"), ("Chandler", "AZ", "85226"), ("Phoenix", "AZ", "85043")
HOU_E, LIT = ("Houston", "TX", "77015"), ("Little Rock", "AR", "72206")
MIA = ("Miami", "FL", "33166")
WWI, CONTOSO, FAB, TAIL = "Wide World Importers", "Contoso Foods", "Fabrikam Building Supply", "Tailspin Toys"
AW, PROSE, FOURTH = "Adventure Works Cycles", "Proseware Chemical", "Fourth Coffee"

# (date, customer, origin, destination, equipment, miles, carrier pay, customer rate)
LANES = [
    ("2026-09-15", WWI, DAL, ATL, "Dry Van", 781, 1875, 2250),
    ("2026-09-02", WWI, DAL, ATL, "Dry Van", 781, 1950, 2300),
    ("2026-08-20", FOURTH, DAL, ATL, "Dry Van", 781, 1800, 2100),
    ("2026-08-05", WWI, DAL, ATL, "Dry Van", 781, 1900, 2250),
    ("2026-07-21", FOURTH, DAL, ATL, "Dry Van", 781, 1850, 2150),
    ("2026-06-10", WWI, DAL, ATL, "Dry Van", 781, 2300, 2700),  # 6th newest: past max_comparables
    ("2026-01-15", WWI, DAL, ATL, "Dry Van", 781, 1500, 1800),  # older than the lookback
    ("2026-09-10", CONTOSO, DAL, ATL, "Reefer", 781, 2400, 2800),  # other equipment
    ("2026-09-08", CONTOSO, FRE, DEN, "Reefer", 1165, 3200, 3750),
    ("2026-08-18", CONTOSO, FRE, DEN, "Reefer", 1165, 3100, 3650),
    ("2026-07-30", CONTOSO, FRE, DEN, "Reefer", 1165, 3250, 3800),
    ("2026-09-11", FAB, BHM, HOU_N, "Flatbed", 675, 1700, 2000),
    ("2026-08-26", FAB, BHM, HOU_N, "Flatbed", 675, 1650, 1950),
    ("2026-08-03", FAB, BHM, HOU_N, "Flatbed", 675, 1750, 2050),
    ("2026-07-14", FAB, BHM, HOU_N, "Flatbed", 675, 1725, 2025),
    ("2026-08-22", FAB, HOU_N, BHM, "Flatbed", 675, 1400, 1650),  # the reverse lane
    ("2026-09-14", TAIL, MEM, CHI, "Van", 532, 1350, 1600),
    ("2026-08-24", TAIL, MEM, CHI, "Van", 532, 1400, 1650),
    ("2026-08-10", TAIL, MEM, CHI, "Van", 532, 1300, 1550),
    ("2026-09-01", FOURTH, GER, OBZ, "Van", 588, 1550, 1800),
    ("2026-07-28", FOURTH, GER, OBZ, "Van", 588, 1500, 1775),
    ("2026-09-09", TAIL, MEM, STL, "Van", 284, 900, 1075),
    ("2026-08-12", TAIL, MEM, STL, "Van", 284, 950, 1125),
    ("2026-09-10", AW, JOL, NSH, "53' Dry Van", 468, 1500, 1800),
    ("2026-08-27", AW, JOL, NSH, "53' Dry Van", 468, 1450, 1750),
    ("2026-08-06", AW, JOL, NSH, "53' Dry Van", 468, 1550, 1825),
    ("2026-08-28", AW, JOL, DET, "53' Dry Van", 285, 1100, 1300),
    ("2026-09-16", WWI, SAV, CLT, "Dry Van", 254, 1050, 1250),
    ("2026-09-03", WWI, SAV, CLT, "Dry Van", 254, 1100, 1300),
    ("2026-08-19", WWI, SAV, CLT, "Dry Van", 254, 1000, 1200),
    ("2026-07-29", WWI, SAV, CLT, "Dry Van", 254, 1150, 1350),
    ("2026-09-04", CONTOSO, SAL, CHD, "Reefer", 702, 2300, 2700),
    ("2026-08-21", CONTOSO, SAL, CHD, "Reefer", 702, 2400, 2800),
    ("2026-08-07", CONTOSO, SAL, CHD, "Reefer", 702, 2350, 2750),
    ("2026-09-12", CONTOSO, SAL, PHX, "Reefer", 680, 2200, 2600),
    ("2026-08-14", CONTOSO, SAL, PHX, "Reefer", 680, 2250, 2650),
    ("2026-09-08", PROSE, HOU_E, LIT, "Van", 437, 1050, 1250),
    ("2026-08-11", PROSE, HOU_E, LIT, "Van", 437, 1100, 1300),
    ("2026-08-30", CONTOSO, ATL, MIA, "Reefer", 662, 1650, 1950),
    ("2026-09-05", TAIL, CHI, DAL, "Van", 925, 1900, 2250),
    ("2026-09-01", CONTOSO, DEN, FRE, "Reefer", 1165, 2100, 2450),
    ("2026-08-17", FAB, BHM, HOU_N, "Hotshot", 675, 1200, 1450),  # equipment the pricing doesn't know: skipped
]


def lanes_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(LANE_HEADER)
    for d, cust, o, dest, eq, miles, pay, rate in LANES:
        w.writerow([d, cust, *o, *dest, eq, miles, f"{pay:.2f}", f"{rate:.2f}"])
    return buf.getvalue()


def _place(p: tuple) -> dict:
    return {"city": p[0], "state": p[1], "zip": p[2]}


def _ship(o, d, pickup, equipment, weight, *, stops=(), pickup_time="", delivery="", delivery_time="",
          commodity="", pieces="", temperature="", hazmat=False, requirements=(), references=(), rate=None,
          truckloads=1, missing=(), total=None, flags=()) -> dict:
    """One lane as the answer key has it: what a perfect reading of the email gives, and the expected outcome."""
    return {"origin": _place(o) if o else {"city": "", "state": "", "zip": ""},
            "destination": _place(d) if d else {"city": "", "state": "", "zip": ""},
            "stops": [_place(s) for s in stops], "pickup_date": pickup, "pickup_time": pickup_time,
            "delivery_date": delivery, "delivery_time": delivery_time, "equipment": equipment, "weight_lbs": weight,
            "commodity": commodity, "pieces": pieces, "temperature": temperature, "hazmat": hazmat,
            "requirements": list(requirements), "references": list(references), "rate": rate,
            "truckloads": truckloads, "missing": list(missing), "total": total, "flags": list(flags)}


def _tender_pdf() -> bytes:
    lines = [
        "## ADVENTURE WORKS CYCLES - LOAD TENDER",
        "Load #: 88213     Tender date: 09/22/2026",
        "Equipment: 53' Dry Van     Weight: 29,750 lbs     Pieces: 18 pallets (boxed bicycles)",
        "",
        "Stop 1 - Pickup: AW Cycles DC, Joliet, IL 60436 | 09/24/2026 08:00 | PO 4471-A, PO 4472-B",
        "Stop 2 - Delivery: Adventure Works Retail, Louisville, KY 40218 | 09/25/2026 07:00 | PO 4471-A",
        "Stop 3 - Delivery: Adventure Works Retail, Nashville, TN 37210 | 09/25/2026 13:00 | PO 4472-B",
        "",
        "Rate: $1,850.00 all-in, including 1 stop-off",
        "Accept by replying to the tender email.",
    ]
    return text_pdf(wrap_page(lines, footer=FOOTER))


def build_inbox() -> list[dict]:
    """The emails: file name, headers, body, attachments and the answer key for each."""
    return [
        dict(file="01_wwi_dallas_atlanta.eml", sender=("Maria Lopez", "maria.lopez@wideworldimporters.example"),
             subject="Rate request: Dallas to Atlanta, Thursday", sent=datetime(2026, 9, 21, 8, 12, tzinfo=CENTRAL),
             body="Hi Coho team,\n\nCan you quote this one?\n\nPick up: Dallas, TX 75207\nDeliver: Atlanta, GA 30318\n"
                  "Pickup Thursday 9/24, 8am-2pm. Deliver by Monday 9/28.\n"
                  "53' dry van, 22 pallets of packaged housewares, 38,500 lbs.\n\n"
                  "Thanks,\nMaria Lopez\nTransportation Coordinator\nWide World Importers",
             kind="quote_request", customer=WWI,
             shipments=[_ship(DAL, ATL, "2026-09-24", "van", 38500, pickup_time="8am-2pm", delivery="2026-09-28",
                              commodity="packaged housewares", pieces="22 pallets", total=2225)]),
        dict(file="02_contoso_fresno_denver.eml", sender=("Jin Park", "jin.park@contosofoods.example"),
             subject="Reefer quote Fresno > Denver", sent=datetime(2026, 9, 21, 10, 40, tzinfo=PACIFIC),
             body="Hello,\n\nLooking for a reefer rate for a pickup tomorrow morning.\n\n"
                  "Fresno, CA 93725 to Denver, CO 80216\n40,000 lbs fresh grapes, 20 pallets\n"
                  "Delivery Thursday 9/24 by 6am.\n\nJin Park\nContoso Foods | Shipping",
             kind="quote_request", customer=CONTOSO,
             shipments=[_ship(FRE, DEN, "2026-09-22", "reefer", 40000, delivery="2026-09-24", delivery_time="by 6am",
                              commodity="fresh grapes", pieces="20 pallets", missing=["temperature"], total=3775)]),
        dict(file="03_fabrikam_flatbed.eml", sender=("Dana Brooks", "dbrooks@fabrikambuilding.example"),
             subject="Flatbed Birmingham AL - Houston TX", sent=datetime(2026, 9, 21, 13, 5, tzinfo=CENTRAL),
             body="Need a flatbed Monday 9/28 out of Birmingham, AL 35203 going to Houston, TX 77029.\n"
                  "46,000 lbs rebar, 12 bundles, tarps required. Consignee receives 7am-3pm.\n\n"
                  "Dana Brooks\nFabrikam Building Supply",
             kind="quote_request", customer=FAB,
             shipments=[_ship(BHM, HOU_N, "2026-09-28", "flatbed", 46000, commodity="rebar", pieces="12 bundles",
                              requirements=["tarps"], total=2150)]),
        dict(file="04_tailspin_three_lanes.eml", sender=("Priya Shah", "priya.shah@tailspintoys.example"),
             subject="Quotes needed - 3 lanes out of Memphis, pickup 9/29",
             sent=datetime(2026, 9, 22, 9, 30, tzinfo=CENTRAL),
             body="Hi,\n\nPlease quote the following, all 53' dry vans picking up at our Memphis, TN 38118 warehouse "
                  "on Tuesday 9/29:\n\n"
                  "1. Memphis, TN -> Chicago, IL 60632 - 24 pallets, 31,000 lbs\n"
                  "2. Memphis, TN -> Columbus, OH 43207 - 26 pallets, 35,200 lbs\n"
                  "3. Memphis, TN -> Kansas City, MO 64120 - 18 pallets, 22,400 lbs\n\n"
                  "Commodity is boxed toys, no hazmat.\n\nThank you,\nPriya Shah\nTailspin Toys",
             kind="quote_request", customer=TAIL,
             shipments=[
                 _ship(MEM, CHI, "2026-09-29", "van", 31000, commodity="boxed toys", pieces="24 pallets", total=1600),
                 _ship(MEM, ("Columbus", "OH", "43207"), "2026-09-29", "van", 35200, commodity="boxed toys",
                       pieces="26 pallets", total=1800, flags=["nearby_zips"]),
                 _ship(MEM, ("Kansas City", "MO", "64120"), "2026-09-29", "van", 22400, commodity="boxed toys",
                       pieces="18 pallets", flags=["state_history_only"]),
             ]),
        dict(file="05_adventure_tender.eml", sender=("Chris Novak", "cnovak@adventure-works.example"),
             subject="Load tender 88213 - Joliet IL to Nashville TN", sent=datetime(2026, 9, 22, 15, 20, tzinfo=CENTRAL),
             body="Hi Coho team,\n\nPlease see the attached tender for load 88213 and confirm by 5pm today.\n\n"
                  "Thanks,\nChris Novak\nLogistics, Adventure Works Cycles",
             attachments=[("LoadTender_88213.pdf", _tender_pdf())],
             kind="tender", customer=AW,
             shipments=[_ship(JOL, NSH, "2026-09-24", "van", 29750, stops=[("Louisville", "KY", "40218")],
                              pickup_time="08:00", delivery="2026-09-25", delivery_time="13:00",
                              commodity="boxed bicycles", pieces="18 pallets",
                              references=["88213", "PO 4471-A", "PO 4472-B"], rate=1850, total=1850)]),
        dict(file="06_wwi_savannah_charlotte.eml", sender=("Maria Lopez", "maria.lopez@wideworldimporters.example"),
             subject="Savannah port pull to Charlotte", sent=datetime(2026, 9, 22, 11, 2, tzinfo=EASTERN),
             body="Hi, need a dry van from Savannah, GA 31408 to Charlotte, NC 28206, pickup Friday 9/25.\n"
                  "47,500 lbs, 1 x 40' container transloaded, floor loaded ceramic tile.\n\n"
                  "Maria Lopez\nWide World Importers",
             kind="quote_request", customer=WWI,
             shipments=[_ship(SAV, CLT, "2026-09-25", "van", 47500, commodity="ceramic tile", total=1275,
                              flags=["overweight"])]),
        dict(file="07_contoso_thread.eml", sender=("Jin Park", "jin.park@contosofoods.example"),
             subject="Re: Salinas to Phoenix reefer", sent=datetime(2026, 9, 23, 7, 55, tzinfo=PACIFIC),
             body="Correction: the consignee is in Tempe, AZ 85281, not Phoenix. And make it 2 loads, same pickup "
                  "day.\n\nJin\n\n"
                  "On Tue, Sep 22, 2026 at 4:10 PM Jin Park <jin.park@contosofoods.example> wrote:\n"
                  "> Need a reefer from Salinas, CA 93901 to Phoenix, AZ 85043 on Thursday 10/1.\n"
                  "> 36,000 lbs bagged salad, keep at 34F continuous.\n> 20 pallets.",
             kind="quote_request", customer=CONTOSO,
             shipments=[_ship(SAL, ("Tempe", "AZ", "85281"), "2026-10-01", "reefer", 36000, commodity="bagged salad",
                              pieces="20 pallets", temperature="34F continuous", truckloads=2, total=2775,
                              flags=["nearby_zips"])]),
        dict(file="08_proseware_hazmat.eml", sender=("Omar Haddad", "ohaddad@proseware.example"),
             subject="Hazmat van Houston to Little Rock", sent=datetime(2026, 9, 23, 9, 15, tzinfo=CENTRAL),
             body="Can you cover a hazmat load from Houston, TX 77015 to Little Rock, AR 72206?\n"
                  "Pickup Monday 9/28, deliver Tuesday 9/29.\n"
                  "Dry van, 36,000 lbs paint, UN1263, Class 3, PG II, 64 drums on 16 pallets. Placards required.\n"
                  "Driver needs a hazmat endorsement and a TWIC card.\n\nOmar Haddad\nProseware Chemical",
             kind="quote_request", customer=PROSE,
             shipments=[_ship(HOU_E, LIT, "2026-09-28", "van", 36000, delivery="2026-09-29", commodity="paint",
                              pieces="64 drums on 16 pallets", hazmat=True, requirements=["TWIC card"], total=1475,
                              flags=["hazmat"])]),
        dict(file="09_carrier_capacity.eml", sender=("Luis Ortega", "dispatch@consolidatedmessenger.example"),
             subject="Truck available Dallas 9/24", sent=datetime(2026, 9, 23, 10, 0, tzinfo=CENTRAL),
             body="Good morning,\n\nWe have an empty 53' van in Dallas, TX on 9/24 looking for a load to Atlanta or "
                  "Memphis. Call dispatch if you have anything.\n\nLuis Ortega\nConsolidated Messenger Trucking",
             kind="other", customer="Consolidated Messenger Trucking", shipments=[]),
        dict(file="10_adventure_small.eml", sender=("Chris Novak", "cnovak@adventure-works.example"),
             subject="Small shipment Joliet to Detroit", sent=datetime(2026, 9, 23, 13, 40, tzinfo=CENTRAL),
             body="Can you move 3 pallets of bike parts, 1,800 lbs, from Joliet, IL 60436 to Detroit, MI 48210?\n"
                  "Pickup Friday 9/25. Liftgate needed at delivery.\n\nChris Novak\nAdventure Works Cycles",
             kind="quote_request", customer=AW,
             shipments=[_ship(JOL, DET, "2026-09-25", "", 1800, commodity="bike parts", pieces="3 pallets",
                              requirements=["liftgate at delivery"], missing=["equipment"],
                              flags=["small_shipment"])]),
        dict(file="11_alpine_phoenix.txt", sender=("Sam Rivera", "sam@alpineskihouse.example"),
             subject="van out of Phoenix", sent=datetime(2026, 9, 23, 16, 5, tzinfo=MOUNTAIN),
             body="Hey - what would you charge for a van out of Phoenix, AZ next week? About 42k lbs of ski racks "
                  "going to the Northeast somewhere; still waiting on the exact consignee.\n\nSam",
             kind="quote_request", customer="Alpine Ski House",
             shipments=[_ship(("Phoenix", "AZ", ""), None, "", "van", 42000, commodity="ski racks",
                              missing=["destination", "pickup_date"])]),
    ]


def _eml(m: dict) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"{m['sender'][0]} <{m['sender'][1]}>"
    msg["To"] = BROKER_EMAIL
    msg["Subject"] = m["subject"]
    msg["Date"] = format_datetime(m["sent"])
    msg["Message-ID"] = f"<{Path(m['file']).stem}@{m['sender'][1].split('@')[1]}>"
    msg.set_content(m["body"] + "\n\n" + FOOTER + "\n")
    for name, data in m.get("attachments", []):
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    if m.get("attachments"):
        msg.set_boundary("freightq-sample-boundary")
    return msg.as_bytes()


def _txt(m: dict) -> str:
    return (f"From: {m['sender'][0]} <{m['sender'][1]}>\nTo: {BROKER_EMAIL}\nSubject: {m['subject']}\n"
            f"Date: {format_datetime(m['sent'])}\n\n{m['body']}\n\n{FOOTER}\n")


def make_sample(out: Path) -> dict:
    """Writes inbox/, lanes.csv, broker.toml and truth.json; returns the answer key."""
    inbox = out / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    emails = build_inbox()
    for m in emails:
        path = inbox / m["file"]
        if path.suffix == ".eml":
            path.write_bytes(_eml(m))
        else:
            path.write_text(_txt(m), encoding="utf-8")
    (out / "lanes.csv").write_text(lanes_csv(), encoding="utf-8")
    (out / "broker.toml").write_text(BROKER_TOML, encoding="utf-8")
    truth = {
        "note": "Answer key for the synthetic inbox made by `python -m freightq sample`. Fictional companies.",
        "emails": [{"file": m["file"], "kind": m["kind"], "customer": m["customer"], "contact": m["sender"][0],
                    "shipments": m["shipments"]} for m in emails],
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=1) + "\n", encoding="utf-8")
    return truth


# --- scoring a run against the answer key -------------------------------------------------------------------------

FIELDS = ["origin", "destination", "stops", "pickup_date", "delivery_date", "equipment", "weight_lbs", "temperature",
          "hazmat", "references", "rate", "truckloads"]


def _same_place(a: dict, b: dict) -> bool:
    return (city_key(a.get("city", "")), a.get("state", ""), a.get("zip", "")) == \
        (city_key(b.get("city", "")), b.get("state", ""), b.get("zip", ""))


def _same(field: str, p, t) -> bool:
    if field in ("origin", "destination"):
        return _same_place(p, t)
    if field == "stops":
        return len(p) == len(t) and all(_same_place(a, b) for a, b in zip(p, t))
    if field in ("weight_lbs", "rate"):
        return (p is None and t is None) or (p is not None and t is not None and abs(p - t) < 0.5)
    if field == "temperature":
        return "".join(c for c in p if c.isdigit() or c == "-") == "".join(c for c in t if c.isdigit() or c == "-")
    if field == "references":
        def norm(refs):
            return {"".join(ch for ch in r.upper() if ch.isalnum() or ch == "-") for r in refs}
        return norm(p) == norm(t)
    return p == t


def _key(s: dict) -> tuple[str, str]:
    return city_key(s["origin"].get("city", "")), city_key(s["destination"].get("city", ""))


def score(results: dict, truth: dict) -> dict:
    pred = {e["file"]: e for e in results["emails"]}
    out = {"emails": len(truth["emails"]), "kind_right": 0, "wrong_kind": [], "lanes": 0, "lanes_found": 0,
           "missed": [], "extra_lanes": 0, "fields": {f: [0, 0] for f in FIELDS}, "missing_right": 0,
           "priced_expected": 0, "rate_right": 0, "wrong_rates": [], "unpriced_right": 0,
           "unpriced_expected": 0, "flags_expected": 0, "flags_found": 0, "missed_flags": [], "extra_flags": []}
    for t in truth["emails"]:
        p = pred.get(t["file"], {"kind": "", "shipments": []})
        if p["kind"] == t["kind"]:
            out["kind_right"] += 1
        else:
            out["wrong_kind"].append(f"{t['file']}: {p['kind'] or 'not read'} (expected {t['kind']})")
        ps = list(p["shipments"])
        out["lanes"] += len(t["shipments"])
        for i, ts in enumerate(t["shipments"], 1):
            m = next((x for x in ps if _key(x) == _key(ts)), None)
            if m is None:
                out["missed"].append(f"{t['file']} lane {i}")
                continue
            ps.remove(m)
            out["lanes_found"] += 1
            for f in FIELDS:
                out["fields"][f][1] += 1
                out["fields"][f][0] += _same(f, m[f], ts[f])
            out["missing_right"] += set(m["missing"]) == set(ts["missing"])
            total = (m.get("quote") or {}).get("total")
            if ts["total"] is not None:
                out["priced_expected"] += 1
                if total is not None and abs(total - ts["total"]) < 0.5:
                    out["rate_right"] += 1
                else:
                    got = "not priced" if total is None else f"${total:,.0f}"
                    out["wrong_rates"].append(f"{t['file']} lane {i}: {got} (expected ${ts['total']:,.0f})")
            else:
                out["unpriced_expected"] += 1
                out["unpriced_right"] += total is None
            codes = {f["code"] for f in m["flags"]}
            out["flags_expected"] += len(ts["flags"])
            out["flags_found"] += len(set(ts["flags"]) & codes)
            out["missed_flags"] += [f"{t['file']} lane {i}: {c}" for c in ts["flags"] if c not in codes]
            out["extra_flags"] += [f"{t['file']} lane {i}: {f['text']}" for f in m["flags"]
                                   if f["code"] not in ts["flags"]]
        out["extra_lanes"] += len(ps)
    return out


def _pct(a: int, b: int) -> str:
    return f"{a} of {b}" + (f" ({100 * a / b:.0f}%)" if b else "")


def format_score(s: dict) -> str:
    lines = [f"Emails read as the right kind: {_pct(s['kind_right'], s['emails'])}"]
    lines += [f"  {w}" for w in s["wrong_kind"]]
    lines.append(f"Lanes found: {_pct(s['lanes_found'], s['lanes'])}; extra lanes: {s['extra_lanes']}")
    lines += [f"  missed: {m}" for m in s["missed"]]
    wrong = [f"{f} {a}/{b}" for f, (a, b) in s["fields"].items() if a < b]
    lines.append("Fields right on found lanes: " + (", ".join(wrong) if wrong else "all"))
    lines.append(f"Missing details right: {_pct(s['missing_right'], s['lanes_found'])}")
    lines.append(f"Rates matching the answer key: {_pct(s['rate_right'], s['priced_expected'])}; "
                 f"left for pricing by hand as expected: {_pct(s['unpriced_right'], s['unpriced_expected'])}")
    lines += [f"  {w}" for w in s["wrong_rates"]]
    lines.append(f"Expected flags raised: {_pct(s['flags_found'], s['flags_expected'])}; "
                 f"other flags: {len(s['extra_flags'])}")
    lines += [f"  missed: {m}" for m in s["missed_flags"]]
    lines += [f"  other: {m}" for m in s["extra_flags"]]
    return "\n".join(lines)
