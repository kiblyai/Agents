"""A synthetic wholesale inbox for a fictional candle brand: catalog, price list, customers, POs and the answer key."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from agentkit.pdfgen import text_pdf, wrap_page
from agentkit.tables import Sheet, xlsx_bytes

EASTERN = timezone(timedelta(hours=-4))
SHOP_EMAIL = "Juniper & Pine Wholesale <wholesale@juniperpine.example>"

SHOP_TOML = """# Settings for one merchant (fictional example).
[shop]
name = "Juniper & Pine Candle Co."
wholesale_discount = 0.5   # wholesale = half of retail for SKUs missing from prices.csv
price_tolerance = 0.01     # flag PO prices more than 1% off
require_po_number = true
min_ship_days = 2          # flag ship dates sooner than this after the PO
tags = ["wholesale", "po-agent"]
shop_domain = "juniper-pine-example.myshopify.com"
"""

CATALOG_HEADER = ["Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published", "Option1 Name",
                  "Option1 Value", "Variant SKU", "Variant Grams", "Variant Inventory Qty", "Variant Price",
                  "Variant Compare At Price", "Variant Barcode", "Image Src", "Status"]
PRODUCTS = [  # handle, title, type, option name, [(value, sku, price, barcode, inventory)], status
    ("cedar-sage-soy-candle", "Cedar & Sage Soy Candle", "Candle", "Size",
     [("8 oz", "JP-CS-08", 24, "850012340011", 140), ("12 oz", "JP-CS-12", 34, "850012340028", 90)], "active"),
    ("fig-leaf-soy-candle", "Fig Leaf Soy Candle", "Candle", "Size",
     [("8 oz", "JP-FL-08", 24, "850012340035", 120), ("12 oz", "JP-FL-12", 34, "850012340042", 60)], "active"),
    ("sea-salt-linen-soy-candle", "Sea Salt Linen Soy Candle", "Candle", "Size",
     [("8 oz", "JP-SL-08", 24, "850012340059", 75), ("12 oz", "JP-SL-12", 34, "850012340066", 48)], "active"),
    ("amber-smoke-soy-candle", "Amber Smoke Soy Candle", "Candle", "Size",
     [("8 oz", "JP-AS-08", 24, "850012340073", 80), ("12 oz", "JP-AS-12", 34, "850012340080", 4)], "active"),
    ("wick-trimmer", "Wick Trimmer", "Accessory", "Finish",
     [("Brass", "JP-WT-BR", 18, "850012340097", 40), ("Black", "JP-WT-BK", 18, "850012340103", 36)], "active"),
    ("safety-matches", "Safety Matches", "Accessory", "Title",
     [("Default Title", "JP-MATCH", 6, "850012340110", 300)], "active"),
    ("holiday-gift-set", "Holiday Gift Set", "Gift Set", "Title",
     [("Default Title", "JP-GIFT-24", 58, "850012340127", 0)], "archived"),
]

PRICES_CSV = """SKU,Wholesale Price,Case Pack,Min Qty,Customer,Buyer SKU
JP-CS-08,12.00,6,,,
JP-CS-12,17.00,6,,,
JP-FL-08,12.00,6,,,
JP-FL-12,17.00,6,,,
JP-SL-08,12.00,6,,,
JP-SL-12,17.00,6,,,
JP-AS-08,12.00,6,,,
JP-AS-12,17.00,6,,,
JP-WT-BR,9.00,4,,,
JP-WT-BK,9.00,4,,,
JP-MATCH,3.00,12,,,
JP-CS-08,11.50,,,Harbor & Vine Mercantile,
JP-FL-08,11.50,,,Harbor & Vine Mercantile,
JP-SL-08,11.50,,,Harbor & Vine Mercantile,
JP-AS-08,11.50,,,Harbor & Vine Mercantile,
JP-CS-12,,,,Willow Street Gifts,WSG-1012
JP-FL-08,,,,Willow Street Gifts,WSG-2008
"""

CUSTOMERS_CSV = """Customer ID,First Name,Last Name,Email,Default Address Company,Default Address City,Default Address Province Code,Tags
1001,Elena,Brooks,orders@harborvine.example,Harbor & Vine Mercantile,Portland,ME,wholesale
1002,Raj,Mehta,buyer@willowstreetgifts.example,Willow Street Gifts,Burlington,VT,wholesale
1003,Jess,Alvarez,jess@paperlantern.example,The Paper Lantern,Asheville,NC,wholesale
1004,Owen,Price,purchasing@coastlinehome.example,Coastline Home Co.,Charleston,SC,wholesale
"""


def catalog_csv() -> str:
    lines = [",".join(CATALOG_HEADER)]
    for handle, title, ptype, opt, variants, status in PRODUCTS:
        for i, (value, sku, price, barcode, inv) in enumerate(variants):
            first = i == 0
            row = [handle, title if first else "", "", "Juniper & Pine" if first else "",
                   ptype if first else "", "", "TRUE" if first else "", opt if first else "", value, sku, "227",
                   str(inv), f"{price:.2f}", "", barcode, "", status if first else ""]
            lines.append(",".join(row))
        if handle == "cedar-sage-soy-candle":  # Shopify adds a row per extra image
            lines.append(",".join([handle] + [""] * 14 + ["https://cdn.example/cedar-sage-2.jpg", ""]))
    return "\n".join(lines) + "\n"


HV_PDF_LINES = [
    "## PURCHASE ORDER", "Harbor & Vine Mercantile", "88 Commercial St, Portland, ME 04101", "",
    "PO Number: HV-2291        PO Date: 10/05/2026", "Vendor: Juniper & Pine Candle Co.",
    "Ship To: Harbor & Vine Mercantile, 88 Commercial St, Portland, ME 04101",
    "Requested Ship Date: 10/16/2026        Terms: Net 30", "",
    "**Item        Description                            Qty    Unit Price    Total",
    "JP-CS-08    Cedar & Sage Soy Candle 8 oz            24     11.50         276.00",
    "JP-FL-08    Fig Leaf Soy Candle 8 oz                24     11.50         276.00",
    "JP-SL-12    Sea Salt Linen Soy Candle 12 oz         12     17.00         204.00",
    "JP-WT-BR    Wick Trimmer - Brass                    8      9.00          72.00", "",
    "Subtotal: 828.00", "Shipping: to be quoted", "Total: 828.00",
]
CH_PDF_LINES = [
    "## Coastline Home Co. - Purchase Order", "PO #: CH-5530        Date: October 8, 2026",
    "Ship to: Coastline Home Co., 210 King St, Charleston, SC 29401", "Ship by: October 23, 2026", "",
    "**SKU         Item                                Qty    Price     Amount",
    "JP-AS-08    Amber Smoke Soy Candle 8oz            24     10.00     240.00",
    "JP-CS-08    Cedar & Sage Soy Candle 8oz           24     12.00     268.00",
    "JP-AS-12    Amber Smoke Soy Candle 12oz           12     17.00     204.00", "",
    "Subtotal                                                          712.00",
]


def _pdf(lines: list[str]) -> bytes:
    return text_pdf(wrap_page(lines))


def _willow_xlsx() -> bytes:
    rows = [["PO #", "WSG-0412", "", "", "", ""],
            ["Ship to", "Willow Street Gifts, 41 Church St, Burlington, VT 05401", "", "", "", ""],
            ["", "", "", "", "", ""],
            ["Our Item #", "UPC", "Description", "Qty", "Unit Cost", "Ext Cost"],
            ["WSG-1012", "", "Cedar Sage 12oz candle", 12, 17, 204],
            ["WSG-2008", "", "Fig Leaf 8oz", 12, 12, 144],
            ["WSG-3104", "850012340097", "Brass wick trimmer", 4, 9, 36],
            ["WSG-3307", "", "Sea Salt Linen candle", 6, 12, 72],
            ["", "", "", "", "Total", 456]]
    return xlsx_bytes([Sheet("Purchase Order", ["Willow Street Gifts - Purchase Order", "", "", "", "", ""], rows)])


def _addr(name, company, address1, city, state, z) -> dict:
    return {"name": name, "company": company, "address1": address1, "address2": "", "city": city, "state": state,
            "zip": z, "country": "US"}


def _line(vendor_sku="", description="", quantity=None, unit_price=None, line_total=None, buyer_sku="", barcode="",
          unit="") -> dict:
    return {"vendor_sku": vendor_sku, "buyer_sku": buyer_sku, "barcode": barcode, "description": description,
            "quantity": quantity, "unit": unit, "unit_price": unit_price, "line_total": line_total}


def _reading(kind, po="", company="", name="", email="", ship=None, ship_date="", terms="", lines=(), subtotal=None,
             total=None) -> dict:
    return {"kind": kind, "po_number": po, "buyer_company": company, "buyer_name": name, "buyer_email": email,
            "ship_to": ship or _addr("", "", "", "", "", ""), "requested_ship_date": ship_date, "payment_terms": terms,
            "lines": list(lines), "subtotal": subtotal, "shipping": None, "tax": None, "total": total, "notes": ""}


def build_inbox() -> list[dict]:
    t = lambda d, h, m: datetime(2026, 10, d, h, m, tzinfo=EASTERN)  # noqa: E731
    hv_ship = _addr("", "Harbor & Vine Mercantile", "88 Commercial St", "Portland", "ME", "04101")
    hv_lines = [_line("JP-CS-08", "Cedar & Sage Soy Candle 8 oz", 24, 11.5, 276),
                _line("JP-FL-08", "Fig Leaf Soy Candle 8 oz", 24, 11.5, 276),
                _line("JP-SL-12", "Sea Salt Linen Soy Candle 12 oz", 12, 17, 204),
                _line("JP-WT-BR", "Wick Trimmer - Brass", 8, 9, 72)]
    hv = _reading("purchase_order", "HV-2291", "Harbor & Vine Mercantile", "Elena Brooks", "orders@harborvine.example",
                  hv_ship, "2026-10-16", "Net 30", hv_lines, 828, 828)
    return [
        dict(file="01_harbor_vine_po.eml", sender=("Elena Brooks", "orders@harborvine.example"), subject="PO HV-2291",
             sent=t(5, 9, 12), attachments=[("PO_HV-2291.pdf", "application/pdf", _pdf(HV_PDF_LINES))],
             body="Hi Juniper & Pine team,\n\nPlease see our attached PO HV-2291 for the holiday season. Ship by "
                  "October 16 please.\n\nThanks,\nElena Brooks\nHarbor & Vine Mercantile",
             reading=hv,
             expect=dict(customer="orders@harborvine.example", ready=True, flags=[],
                         lines=[("JP-CS-08", 24, 11.5), ("JP-FL-08", 24, 11.5), ("JP-SL-12", 12, 17.0),
                                ("JP-WT-BR", 8, 9.0)])),
        dict(file="02_willow_street_po.eml", sender=("Raj Mehta", "buyer@willowstreetgifts.example"),
             subject="Willow Street Gifts PO WSG-0412", sent=t(6, 10, 30),
             attachments=[("WSG-0412.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           _willow_xlsx())],
             body="Hi,\n\nPO attached. Please ship to our store by 10/20/2026.\n\nRaj",
             reading=_reading("purchase_order", "WSG-0412", "Willow Street Gifts", "Raj Mehta",
                              "buyer@willowstreetgifts.example",
                              _addr("", "Willow Street Gifts", "41 Church St", "Burlington", "VT", "05401"),
                              "2026-10-20", "",
                              [_line("", "Cedar Sage 12oz candle", 12, 17, 204, buyer_sku="WSG-1012"),
                               _line("", "Fig Leaf 8oz", 12, 12, 144, buyer_sku="WSG-2008"),
                               _line("", "Brass wick trimmer", 4, 9, 36, buyer_sku="WSG-3104", barcode="850012340097"),
                               _line("", "Sea Salt Linen candle", 6, 12, 72, buyer_sku="WSG-3307")], None, 456),
             expect=dict(customer="buyer@willowstreetgifts.example", ready=False, flags=["ambiguous"],
                         lines=[("JP-CS-12", 12, 17.0), ("JP-FL-08", 12, 12.0), ("JP-WT-BR", 4, 9.0)])),
        dict(file="03_paper_lantern.txt", sender=("Jess Alvarez", "jess@paperlantern.example"),
             subject="Order for The Paper Lantern - PO 7781", sent=t(7, 14, 5), attachments=[],
             body="Hi there,\n\nPlease send the following on PO 7781:\n\n"
                  "12 x Fig Leaf Soy Candle 12 oz (JP-FL-12) @ $17.00\n6 x Safety Matches (JP-MATCH) @ $3.00\n\n"
                  "Ship to: The Paper Lantern, 17 Lexington Ave, Asheville, NC 28801\nNet 30 as usual. Thanks!\nJess",
             reading=_reading("purchase_order", "7781", "The Paper Lantern", "Jess Alvarez", "jess@paperlantern.example",
                              _addr("", "The Paper Lantern", "17 Lexington Ave", "Asheville", "NC", "28801"), "",
                              "Net 30", [_line("JP-FL-12", "Fig Leaf Soy Candle 12 oz", 12, 17),
                                         _line("JP-MATCH", "Safety Matches", 6, 3)]),
             expect=dict(customer="jess@paperlantern.example", ready=False, flags=["case_pack"],
                         lines=[("JP-FL-12", 12, 17.0), ("JP-MATCH", 6, 3.0)])),
        dict(file="04_coastline_po.eml", sender=("Owen Price", "purchasing@coastlinehome.example"), subject="PO CH-5530",
             sent=t(8, 11, 0), attachments=[("PO_CH-5530.pdf", "application/pdf", _pdf(CH_PDF_LINES))],
             body="Hello,\n\nAttached is PO CH-5530. Please confirm.\n\nOwen Price\nCoastline Home Co.",
             reading=_reading("purchase_order", "CH-5530", "Coastline Home Co.", "Owen Price",
                              "purchasing@coastlinehome.example",
                              _addr("", "Coastline Home Co.", "210 King St", "Charleston", "SC", "29401"), "2026-10-23",
                              "", [_line("JP-AS-08", "Amber Smoke Soy Candle 8oz", 24, 10, 240),
                                   _line("JP-CS-08", "Cedar & Sage Soy Candle 8oz", 24, 12, 268),
                                   _line("JP-AS-12", "Amber Smoke Soy Candle 12oz", 12, 17, 204)], 712),
             expect=dict(customer="purchasing@coastlinehome.example", ready=False,
                         flags=["price_mismatch", "line_math", "short_stock"],
                         lines=[("JP-AS-08", 24, 12.0), ("JP-CS-08", 24, 12.0), ("JP-AS-12", 12, 17.0)])),
        dict(file="05_harbor_vine_resend.eml", sender=("Elena Brooks", "orders@harborvine.example"),
             subject="Fwd: PO HV-2291", sent=t(9, 8, 45),
             attachments=[("PO_HV-2291.pdf", "application/pdf", _pdf(HV_PDF_LINES))],
             body="Hi again - resending PO HV-2291 in case it got lost. Can you confirm it ships by the 16th?\n\nElena",
             reading=hv,
             expect=dict(customer="orders@harborvine.example", ready=False, flags=["duplicate"],
                         lines=[("JP-CS-08", 24, 11.5), ("JP-FL-08", 24, 11.5), ("JP-SL-12", 12, 17.0),
                                ("JP-WT-BR", 8, 9.0)])),
        dict(file="06_maple_row_new.eml", sender=("Dana Whitaker", "dana@maplerowboutique.example"),
             subject="New order - Maple Row Boutique", sent=t(9, 13, 20), attachments=[],
             body="Hello! We met at the Portland gift show and would love to carry your candles. Our first PO is "
                  "below.\n\nPO: MR-1001\nJP-GIFT-24 Holiday Gift Set x 6\nJP-LAV-08 Lavender Soy Candle 8 oz x 12\n"
                  "JP-SL-08 Sea Salt Linen Soy Candle 8 oz x 12\n\n"
                  "Ship to: Maple Row Boutique, 5 Main St, Stowe, VT 05672\n\nThank you,\nDana Whitaker",
             reading=_reading("purchase_order", "MR-1001", "Maple Row Boutique", "Dana Whitaker",
                              "dana@maplerowboutique.example",
                              _addr("", "Maple Row Boutique", "5 Main St", "Stowe", "VT", "05672"), "", "",
                              [_line("JP-GIFT-24", "Holiday Gift Set", 6),
                               _line("JP-LAV-08", "Lavender Soy Candle 8 oz", 12),
                               _line("JP-SL-08", "Sea Salt Linen Soy Candle 8 oz", 12)]),
             expect=dict(customer="", ready=False, flags=["new_customer", "archived", "unmatched", "no_po_price"],
                         lines=[("JP-GIFT-24", 6, 29.0), ("JP-SL-08", 12, 12.0)])),
        dict(file="07_willow_reorder.txt", sender=("Raj Mehta", "buyer@willowstreetgifts.example"), subject="Reorder",
             sent=t(9, 16, 40), attachments=[],
             body="Hi - could you send the same as our last order? Same ship-to.\n\nThanks,\nRaj",
             reading=_reading("reorder", "", "Willow Street Gifts", "Raj Mehta", "buyer@willowstreetgifts.example"),
             expect=dict(customer="buyer@willowstreetgifts.example", ready=False, flags=["reorder"], lines=[])),
    ]


def _eml(m: dict) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"{m['sender'][0]} <{m['sender'][1]}>"
    msg["To"] = SHOP_EMAIL
    msg["Subject"] = m["subject"]
    msg["Date"] = format_datetime(m["sent"])
    msg["Message-ID"] = f"<{Path(m['file']).stem}@{m['sender'][1].split('@')[1]}>"
    msg.set_content(m["body"] + "\n")
    for name, ctype, data in m["attachments"]:
        main, sub = ctype.split("/")
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    if m["attachments"]:
        msg.set_boundary("shopifypo-sample-boundary")
    return msg.as_bytes()


def _txt(m: dict) -> str:
    return (f"From: {m['sender'][0]} <{m['sender'][1]}>\nTo: {SHOP_EMAIL}\nSubject: {m['subject']}\n"
            f"Date: {format_datetime(m['sent'])}\n\n{m['body']}\n")


def make_sample(out: Path) -> dict:
    """Writes inbox/, catalog.csv, prices.csv, customers.csv, shop.toml and truth.json; returns the answer key."""
    inbox = out / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    emails = build_inbox()
    for m in emails:
        path = inbox / m["file"]
        if path.suffix == ".eml":
            path.write_bytes(_eml(m))
        else:
            path.write_text(_txt(m), encoding="utf-8")
    (out / "catalog.csv").write_text(catalog_csv(), encoding="utf-8")
    (out / "prices.csv").write_text(PRICES_CSV, encoding="utf-8")
    (out / "customers.csv").write_text(CUSTOMERS_CSV, encoding="utf-8")
    (out / "shop.toml").write_text(SHOP_TOML, encoding="utf-8")
    truth = {
        "note": "Answer key for the synthetic wholesale inbox made by `python -m shopifypo sample`. Fictional companies. "
                "`reading` is what a perfect model returns; `expect` is what the checks should then produce.",
        "emails": [{"file": m["file"], "reading": m["reading"],
                    "expect": {**m["expect"], "lines": [{"sku": s, "units": u, "unit_price": p}
                                                        for s, u, p in m["expect"]["lines"]]}} for m in emails],
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=1) + "\n", encoding="utf-8")
    return truth


# --- scoring a run against the answer key -------------------------------------------------------------------------

def score(results: dict, truth: dict) -> dict:
    pred = {o["file"]: o for o in results["orders"]}
    out = {"emails": len(truth["emails"]), "kind_right": 0, "wrong_kind": [], "lines": 0, "lines_found": 0,
           "missed_lines": [], "price_right": 0, "wrong_prices": [], "ready_right": 0, "wrong_ready": [],
           "customer_right": 0, "flags_expected": 0, "flags_found": 0, "missed_flags": [], "extra_holds": []}
    for t in truth["emails"]:
        p = pred.get(t["file"], {"kind": "", "lines": [], "flags": [], "ready": False, "customer": ""})
        e = t["expect"]
        if p["kind"] == t["reading"]["kind"]:
            out["kind_right"] += 1
        else:
            out["wrong_kind"].append(f"{t['file']}: {p['kind'] or 'not read'} (expected {t['reading']['kind']})")
        out["customer_right"] += p.get("customer", "") == e["customer"]
        if bool(p["ready"]) == e["ready"]:
            out["ready_right"] += 1
        else:
            out["wrong_ready"].append(f"{t['file']}: {'ready' if p['ready'] else 'held'} (expected "
                                      f"{'ready' if e['ready'] else 'held'})")
        got = {(ln["sku"], ln["units"]): ln for ln in p["lines"] if ln.get("sku")}
        for x in e["lines"]:
            out["lines"] += 1
            ln = got.get((x["sku"], x["units"]))
            if ln is None:
                out["missed_lines"].append(f"{t['file']}: {x['units']} x {x['sku']}")
                continue
            out["lines_found"] += 1
            if ln.get("unit_price") is not None and abs(ln["unit_price"] - x["unit_price"]) < 0.005:
                out["price_right"] += 1
            else:
                out["wrong_prices"].append(f"{t['file']} {x['sku']}: {ln.get('unit_price')} (expected {x['unit_price']})")
        codes = {f["code"] for f in p["flags"]} | {f["code"] for ln in p["lines"] for f in ln["flags"]}
        holds = {f["code"] for f in p["flags"] if f["hold"]} | {f["code"] for ln in p["lines"] for f in ln["flags"]
                                                                  if f["hold"]}
        out["flags_expected"] += len(e["flags"])
        out["flags_found"] += len(set(e["flags"]) & codes)
        out["missed_flags"] += [f"{t['file']}: {c}" for c in e["flags"] if c not in codes]
        out["extra_holds"] += [f"{t['file']}: {c}" for c in sorted(holds - set(e["flags"]))]
    return out


def _pct(a: int, b: int) -> str:
    return f"{a} of {b}" + (f" ({100 * a / b:.0f}%)" if b else "")


def format_score(s: dict) -> str:
    lines = [f"Emails read as the right kind: {_pct(s['kind_right'], s['emails'])}"]
    lines += [f"  {w}" for w in s["wrong_kind"]]
    lines.append(f"Lines matched to the right SKU and quantity: {_pct(s['lines_found'], s['lines'])}")
    lines += [f"  missed: {m}" for m in s["missed_lines"]]
    lines.append(f"Prices right on matched lines: {_pct(s['price_right'], s['lines_found'])}")
    lines += [f"  {w}" for w in s["wrong_prices"]]
    lines.append(f"Shopify customer found: {_pct(s['customer_right'], s['emails'])}")
    lines.append(f"Ready / held as expected: {_pct(s['ready_right'], s['emails'])}")
    lines += [f"  {w}" for w in s["wrong_ready"]]
    lines.append(f"Expected checks raised: {_pct(s['flags_found'], s['flags_expected'])}; other must-fix checks: "
                 f"{len(s['extra_holds'])}")
    lines += [f"  missed: {m}" for m in s["missed_flags"]] + [f"  other: {m}" for m in s["extra_holds"]]
    return "\n".join(lines)
