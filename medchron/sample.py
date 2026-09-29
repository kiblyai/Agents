"""A synthetic personal-injury case file (fictional patient) with an answer key, and a scorer for runs on it.

The records are clean text PDFs, so they test the extraction and checks, not OCR. Everything here is made up.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .extract import name_tokens
from .pdfgen import text_pdf, wrap_page

PATIENT = "Dana Whitfield"
DOB = date(1986, 8, 2)
DOI = date(2025, 3, 14)
FOOTER = "SYNTHETIC RECORD - FICTIONAL PATIENT - NOT REAL MEDICAL DATA"
GAP_DAYS = 57  # the planted treatment gap before the injection


@dataclass
class Facility:
    name: str
    address: str
    phone: str
    mrn: str
    style: str  # how this practice writes dates


def fmt(d: date, style: str) -> str:
    if style == "mdy":
        return f"{d.month}/{d.day}/{d.year % 100:02d}"
    if style == "BdY":
        return f"{d.strftime('%B')} {d.day}, {d.year}"
    if style == "iso":
        return d.isoformat()
    if style == "dbY":
        return f"{d.day:02d}-{d.strftime('%b')}-{d.year}"
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


HOSPITAL = Facility("Maple Falls General Hospital", "100 Harbor Road, Maple Falls", "555-0100", "MFG-448120", "mdY")
PCP = Facility("Keller Family Medicine", "22 Birch Street, Maple Falls", "555-0142", "KFM-20931", "BdY")
PT = Facility("Maple Falls Physical Therapy", "8 Mill Lane, Maple Falls", "555-0177", "PT-7731", "mdy")
ORTHO = Facility("Northside Orthopedic Associates", "410 North Avenue, Maple Falls", "555-0190", "NOA-5528", "mdY")
IMAGING = Facility("Clearview Imaging Center", "75 Lake Drive, Maple Falls", "555-0123", "CIC-99102", "iso")
PAIN = Facility("Summit Pain and Spine", "300 Summit Way, Maple Falls", "555-0165", "SPS-3310", "dbY")


def header(f: Facility, patient: str = PATIENT, dob: date = DOB) -> list[str]:
    return [f"## {f.name}", f"{f.address} | Phone {f.phone}",
            f"Patient: {patient}   DOB: {fmt(dob, f.style)}   MRN: {f.mrn}", ""]


@dataclass
class Doc:
    name: str
    pages: list[list[str]] = field(default_factory=list)  # logical pages

    def add(self, lines: list[str]) -> int:
        self.pages.append(lines)
        return len(self.pages) - 1


@dataclass
class Case:
    docs: list[Doc] = field(default_factory=list)
    encounters: list[dict] = field(default_factory=list)  # with "doc" and "logical" page refs until laid out
    bills: list[dict] = field(default_factory=list)
    no_content: list[tuple[str, int]] = field(default_factory=list)
    other_patient: list[tuple[str, int]] = field(default_factory=list)
    duplicates: list[tuple[str, int]] = field(default_factory=list)

    def encounter(self, doc: Doc, logical: list[int], d: date, provider: str, facility: Facility, visit_type: str,
                  icd: list[str], pre_incident: bool = False) -> None:
        self.encounters.append({"date": d.isoformat(), "provider": provider, "facility": facility.name,
                                "visit_type": visit_type, "icd": icd, "doc": doc.name, "logical": logical,
                                "pre_incident": pre_incident})

    def bill_page(self, doc: Doc, f: Facility, lines: list[tuple[date, str, str, int, float]]) -> None:
        body = header(f) + ["**ITEMIZED STATEMENT OF CHARGES", f"Statement date: {fmt(date(2025, 8, 1), f.style)}", "",
                            "**Date of service | Code | Description | Units | Charge"]
        for d, code, desc, units, charge in lines:
            body.append(f"{fmt(d, f.style)} | {code} | {desc} | {units} | ${charge:,.2f}")
            self.bills.append({"date": d.isoformat(), "provider": f.name, "code": code, "charge": charge})
        total = sum(x[4] for x in lines)
        body += ["", f"**TOTAL CHARGES: ${total:,.2f}", "Payments and adjustments: $0.00", f"Balance due: ${total:,.2f}"]
        doc.add(body)


def build_case(pt_visits: int = 8) -> Case:
    if not 2 <= pt_visits <= 120:
        raise ValueError("pt_visits must be between 2 and 120")
    c = Case()
    raman, benson, keller = "Priya Raman, MD", "Laura Benson, MD", "Thomas Keller, DO"
    chen, vasquez, ortiz, okafor = "Mark Chen, PT, DPT", "Elena Vasquez, MD", "Rafael Ortiz, MD", "Samuel Okafor, MD"
    h = HOSPITAL.style

    # 1. Hospital: fax cover, emergency visit (2 pages), CT report, bill
    er = Doc("01_maple_falls_general_hospital.pdf")
    c.docs.append(er)
    c.no_content.append((er.name, er.add([
        "## FAX TRANSMITTAL", "", f"Date: {fmt(date(2025, 8, 5), h)}", "To: Records Department, Hale & Ostrow LLP",
        "From: Health Information Management, Maple Falls General Hospital", f"Re: Records request for {PATIENT}",
        "Pages (including cover): 5", "",
        "CONFIDENTIALITY NOTICE: This fax contains protected health information. If you received it in error, "
        "notify the sender and destroy all copies."])))
    ed_p1 = header(HOSPITAL) + [
        "**EMERGENCY DEPARTMENT PHYSICIAN RECORD", f"Date of service: {fmt(DOI, h)}   Arrival: 18:42",
        f"Attending physician: {raman}", "",
        "Chief complaint: Neck pain and headache after a motor vehicle collision.",
        "History of present illness: 38-year-old restrained driver, rear-ended at a stoplight at about 17:30 today. "
        "Airbags did not deploy. Reports neck pain 7/10 radiating to the right shoulder, and a headache. "
        "Denies loss of consciousness, chest pain or abdominal pain.",
        "Past medical history: Low back pain in 2024, resolved. No prior neck injury.",
        "Vitals: BP 138/86, HR 92, Temp 98.4 F, SpO2 99% on room air.",
        "Exam: Tenderness over the cervical paraspinal muscles C4-C7, right more than left. Range of motion limited "
        "by pain. No midline tenderness. Neurologically intact. Lumbar spine non-tender.",
        "ED course: Ketorolac 30 mg IM given at 19:10 with partial relief. CT cervical spine ordered."]
    ed_p2 = header(HOSPITAL) + [
        "**EMERGENCY DEPARTMENT PHYSICIAN RECORD (continued) - page 2 of 2", "",
        "Imaging: CT cervical spine without contrast: no fracture or malalignment (see radiology report).",
        "Diagnoses:", "  S13.4XXA Sprain of ligaments of cervical spine, initial encounter",
        "  S16.1XXA Strain of muscle, fascia and tendon at neck level, initial encounter",
        "  R51.9 Headache, unspecified",
        "Prescriptions: Ibuprofen 600 mg by mouth every 8 hours as needed; cyclobenzaprine 5 mg at bedtime.",
        "Disposition: Discharged home in stable condition at 21:15.",
        f"Work: Off work {fmt(DOI + timedelta(1), h)} through {fmt(DOI + timedelta(3), h)}.",
        "Follow up with primary care physician in 3-5 days. Return for worsening symptoms.", "",
        f"Electronically signed: {raman} {fmt(DOI, h)} 21:20"]
    first = er.add(ed_p1)
    er.add(ed_p2)
    c.encounter(er, [first, first + 1], DOI, raman, HOSPITAL, "Emergency department visit",
                ["S13.4XXA", "S16.1XXA", "R51.9"])
    ct = er.add(header(HOSPITAL) + [
        "**RADIOLOGY REPORT", "Exam: CT CERVICAL SPINE WITHOUT CONTRAST", f"Exam date: {fmt(DOI, h)} 19:55",
        f"Ordering physician: {raman}", f"Radiologist: {benson}", "", "Clinical history: Motor vehicle collision, neck pain.",
        "Findings: No acute fracture. Alignment is normal. Mild degenerative disc space narrowing at C5-C6. "
        "Prevertebral soft tissues are normal.",
        "Impression:", "  1. No acute fracture or malalignment of the cervical spine.",
        "  2. Mild degenerative change at C5-C6.", "", f"Electronically signed: {benson} {fmt(DOI, h)} 20:31"])
    c.encounter(er, [ct], DOI, benson, HOSPITAL, "CT cervical spine", [])
    c.bill_page(er, HOSPITAL, [(DOI, "99284", "Emergency department visit, high severity", 1, 1450.00),
                               (DOI, "72125", "CT cervical spine without contrast", 1, 2180.00),
                               (DOI, "96372", "Therapeutic injection, intramuscular", 1, 152.00),
                               (DOI, "J1885", "Ketorolac injection, per 15 mg", 2, 46.00)])

    # 2. Primary care: records certification, a visit before the injury, follow-up, bill
    pcp = Doc("02_keller_family_medicine.pdf")
    c.docs.append(pcp)
    p = PCP.style
    c.no_content.append((pcp.name, pcp.add(header(PCP) + [
        "**CERTIFICATION OF MEDICAL RECORDS", "",
        f"I certify that the attached records for {PATIENT} are true copies kept in the regular course of business.",
        f"Signed: Jean Porter, Records Custodian, {fmt(date(2025, 8, 12), p)}"])))
    prior = date(2024, 6, 10)
    i = pcp.add(header(PCP) + [
        "**OFFICE VISIT", f"Date of visit: {fmt(prior, p)}", f"Provider: {keller}", "",
        "Complaint: Low back pain for 2 weeks after moving furniture, 4/10, no radiation.",
        "Exam: Lumbar paraspinal tenderness. Full range of motion. Straight leg raise negative.",
        "Assessment: M54.50 Low back pain, unspecified",
        "Plan: Ibuprofen 400 mg as needed and home exercises. Return as needed.", "",
        f"Signed: {keller} {fmt(prior, p)}"])
    c.encounter(pcp, [i], prior, keller, PCP, "Office visit", ["M54.50"], pre_incident=True)
    fu = DOI + timedelta(6)
    i = pcp.add(header(PCP) + [
        "**OFFICE VISIT", f"Date of visit: {fmt(fu, p)}", f"Provider: {keller}", "",
        f"Reason: Follow-up after a motor vehicle collision on {fmt(DOI, p)}, seen in the emergency department.",
        "Complaints: Neck pain 6/10 with stiffness; new low back pain 5/10 since the collision; headaches improving.",
        "Exam: Decreased cervical rotation to the right. Lumbar paraspinal spasm. Strength 5/5 in all extremities.",
        "Assessment:", "  S13.4XXA Sprain of ligaments of cervical spine, initial encounter",
        "  S33.5XXA Sprain of ligaments of lumbar spine, initial encounter",
        "Plan: Physical therapy 2 times a week for 4 weeks. Refer to orthopedics. Continue cyclobenzaprine 5 mg at "
        "bedtime.", "Work status: Light duty, no lifting over 10 lbs, until re-evaluated.", "",
        f"Signed: {keller} {fmt(fu, p)}"])
    c.encounter(pcp, [i], fu, keller, PCP, "Office visit", ["S13.4XXA", "S33.5XXA"])
    c.bill_page(pcp, PCP, [(fu, "99214", "Office visit, established patient, moderate", 1, 245.00)])

    # 3. Physical therapy: evaluation (2 pages), daily notes, one misfiled page for another patient, ledger
    pt = Doc("03_maple_falls_physical_therapy.pdf")
    c.docs.append(pt)
    s = PT.style
    ev = DOI + timedelta(10)  # a Monday
    first = pt.add(header(PT) + [
        "**PHYSICAL THERAPY INITIAL EVALUATION", f"Date: {fmt(ev, s)}", f"Therapist: {chen}",
        f"Referring provider: {keller}", "",
        f"Subjective: Neck pain 6/10 and low back pain 5/10 since a motor vehicle collision on {fmt(DOI, s)}. "
        "Difficulty turning the head while driving and sitting longer than 30 minutes.",
        "Objective: Cervical rotation right 45 degrees, left 60 degrees. Lumbar flexion limited by 50%. "
        "Tender cervical and lumbar paraspinals."])
    pt.add(header(PT) + [
        "**PHYSICAL THERAPY INITIAL EVALUATION (continued)", "",
        "Assessment: Impairments consistent with cervical and lumbar sprain. Good rehabilitation potential.",
        "Diagnoses: S13.4XXA Sprain of ligaments of cervical spine; S33.5XXA Sprain of ligaments of lumbar spine",
        "Plan: 2 times a week for 4 weeks: therapeutic exercise, manual therapy and a home program.",
        "Goals: Pain 2/10 or less; full cervical rotation; sit 60 minutes without pain.", "",
        f"Signed: {chen} {fmt(ev, s)}"])
    c.encounter(pt, [first, first + 1], ev, chen, PT, "Physical therapy evaluation", ["S13.4XXA", "S33.5XXA"])
    visits = [ev]
    for n in range(1, pt_visits):
        visits.append(visits[-1] + timedelta(3 if n % 2 else 4))  # Mondays and Thursdays
    ledger = [(ev, "97161", "PT evaluation, low complexity", 1, 210.00)]
    for n, d in enumerate(visits[1:], 2):
        neck = max(2, 6 - n // 2)
        back = max(1, 5 - n // 2)
        last = n == len(visits)
        i = pt.add(header(PT) + [
            f"**PHYSICAL THERAPY DAILY NOTE - visit {n}", f"Date: {fmt(d, s)}", f"Therapist: {chen}", "",
            f"Pain today: neck {neck}/10, low back {back}/10.",
            "Treatment: Therapeutic exercise 30 minutes (97110 x2); manual therapy 15 minutes (97140 x1).",
            "Response: Tolerated well. Cervical rotation improving.",
            "Plan: Continue plan of care." + (" Patient did not attend further scheduled visits." if last else ""), "",
            f"Signed: {chen} {fmt(d, s)}"])
        c.encounter(pt, [i], d, chen, PT, "Physical therapy", [])
        ledger += [(d, "97110", "Therapeutic exercise, 15 minutes", 2, 190.00),
                   (d, "97140", "Manual therapy, 15 minutes", 1, 85.00)]
        if n == 4:  # a page from another patient's chart, filed here by mistake
            c.other_patient.append((pt.name, pt.add(header(PT, "Robert Hale", date(1971, 1, 9)) + [
                "**PHYSICAL THERAPY DAILY NOTE - visit 6", f"Date: {fmt(d + timedelta(1), s)}", f"Therapist: {chen}", "",
                "Pain today: right knee 3/10.", "Treatment: Therapeutic exercise 30 minutes (97110 x2).",
                "Plan: Continue plan of care.", "", f"Signed: {chen} {fmt(d + timedelta(1), s)}"])))
    c.bill_page(pt, PT, ledger)

    # 4. Orthopedics: consultation (2 pages), follow-up after the injection, bill
    ortho = Doc("04_northside_orthopedic_associates.pdf")
    c.docs.append(ortho)
    o = ORTHO.style
    consult = ev + timedelta(22)
    mri = consult + timedelta(7)
    injection = max(mri, visits[-1]) + timedelta(GAP_DAYS)
    follow = injection + timedelta(21)
    first = ortho.add(header(ORTHO) + [
        "**ORTHOPEDIC CONSULTATION", f"Date of visit: {fmt(consult, o)}", f"Physician: {vasquez}",
        f"Referred by: {keller}", "",
        f"History: Motor vehicle collision on {fmt(DOI, o)}. Persistent neck pain 5/10 radiating to the right arm, "
        "with intermittent numbness in the right thumb and index finger. Physical therapy 2 times a week with "
        "partial improvement.",
        "Exam: Spurling test positive on the right. Decreased sensation in the right C6 dermatome. Grip strength "
        "4+/5 on the right."])
    ortho.add(header(ORTHO) + [
        "**ORTHOPEDIC CONSULTATION (continued)", "",
        f"Imaging reviewed: CT cervical spine from {fmt(DOI, o)}, no fracture.",
        "Assessment:", "  M54.12 Radiculopathy, cervical region",
        "  S13.4XXA Sprain of ligaments of cervical spine, initial encounter",
        "Plan: MRI cervical spine without contrast. Continue physical therapy. Meloxicam 15 mg daily.",
        "Work status: Light duty, no lifting over 15 lbs, no overhead work.", "Return after the MRI.", "",
        f"Signed: {vasquez} {fmt(consult, o)}"])
    c.encounter(ortho, [first, first + 1], consult, vasquez, ORTHO, "Orthopedic consultation", ["M54.12", "S13.4XXA"])

    # 5. Imaging: MRI report, bill (added to the case before the orthopedic follow-up, in file order)
    img = Doc("05_clearview_imaging_center.pdf")
    i = img.add(header(IMAGING) + [
        "**MRI CERVICAL SPINE WITHOUT CONTRAST", f"Exam date: {fmt(mri, IMAGING.style)}",
        f"Ordering physician: {vasquez}", f"Radiologist: {ortiz}", "",
        "Clinical history: Neck pain with right arm numbness after a motor vehicle collision.",
        "Findings: C5-C6: 3 mm right paracentral disc protrusion contacting the right C6 nerve root. "
        "C4-C5 and C6-C7: no significant stenosis. Spinal cord normal in signal.",
        "Impression: C5-C6 right paracentral disc protrusion (3 mm) contacting the right C6 nerve root.",
        "ICD-10: M50.222 Other cervical disc displacement at C5-C6 level", "",
        f"Electronically signed: {ortiz} {fmt(mri, IMAGING.style)}"])
    c.encounter(img, [i], mri, ortiz, IMAGING, "MRI cervical spine", ["M50.222"])
    c.bill_page(img, IMAGING, [(mri, "72141", "MRI cervical spine without contrast", 1, 1875.00)])

    i = ortho.add(header(ORTHO) + [
        "**ORTHOPEDIC FOLLOW-UP", f"Date of visit: {fmt(follow, o)}", f"Physician: {vasquez}", "",
        "Interval history: Neck pain 2/10 after the epidural steroid injection. Numbness resolved.",
        "Exam: Spurling test negative. Sensation intact. Strength 5/5.",
        "Assessment:", "  M50.222 Other cervical disc displacement at C5-C6 level, improved",
        "  M54.12 Radiculopathy, cervical region, resolving",
        "Plan: Home exercise program. Return as needed.",
        f"Work status: Full duty without restrictions as of {fmt(follow, o)}.", "",
        f"Signed: {vasquez} {fmt(follow, o)}"])
    c.encounter(ortho, [i], follow, vasquez, ORTHO, "Orthopedic follow-up", ["M50.222", "M54.12"])
    c.bill_page(ortho, ORTHO, [(consult, "99204", "New patient visit, moderate", 1, 425.00),
                               (follow, "99213", "Office visit, established patient", 1, 180.00)])
    c.docs.append(img)

    # 6. Pain management: injection (2 pages), bill
    pain = Doc("06_summit_pain_and_spine.pdf")
    c.docs.append(pain)
    q = PAIN.style
    first = pain.add(header(PAIN) + [
        "**PROCEDURE NOTE", f"Date of procedure: {fmt(injection, q)}", f"Physician: {okafor}", "",
        "Procedure: Cervical interlaminar epidural steroid injection at C6-C7 under fluoroscopy.",
        "Indication: Right C6 radiculopathy with C5-C6 disc protrusion on MRI, not improved with therapy.",
        "Pre-procedure pain: 6/10 in the neck and right arm.",
        "Medication: Dexamethasone 10 mg with 2 mL preservative-free saline.",
        "Complications: None. Post-procedure pain 2/10."])
    pain.add(header(PAIN) + [
        "**PROCEDURE NOTE (continued)", "",
        "Diagnoses:", "  M50.222 Other cervical disc displacement at C5-C6 level",
        "  M54.12 Radiculopathy, cervical region",
        "Plan: Follow up with orthopedics in 3 weeks. May repeat the injection if symptoms return.",
        f"Work status: Off work on the day of the procedure; may return {fmt(injection + timedelta(1), q)}.", "",
        f"Signed: {okafor} {fmt(injection, q)}"])
    c.encounter(pain, [first, first + 1], injection, okafor, PAIN, "Epidural steroid injection",
                ["M50.222", "M54.12"])
    c.bill_page(pain, PAIN, [(injection, "62321", "Cervical epidural injection with imaging guidance", 1, 3200.00),
                             (injection, "J1100", "Dexamethasone injection, 1 mg", 10, 40.00)])

    # 7. A second copy of the emergency visit, from the insurer's claim file
    dup = Doc("07_records_from_insurer_claim_file.pdf")
    c.docs.append(dup)
    for lines in (ed_p1, ed_p2):
        c.duplicates.append((dup.name, dup.add(["Received from Lakeshore Mutual claim file 25-00417", *lines])))
    return c


def make_sample(out: Path, pt_visits: int = 8) -> dict:
    """Write records/*.pdf and truth.json (the answer key) into `out`."""
    case = build_case(pt_visits)
    rec = out / "records"
    rec.mkdir(parents=True, exist_ok=True)
    for old in rec.glob("*.pdf"):
        old.unlink()
    where: dict[tuple[str, int], list[int]] = {}  # (doc, logical page) -> page numbers in the combined PDF
    n = 0
    for doc in case.docs:
        printed = []
        for k, lines in enumerate(doc.pages):
            pages = wrap_page(lines, FOOTER)
            where[(doc.name, k)] = list(range(n + len(printed) + 1, n + len(printed) + len(pages) + 1))
            printed += pages
        (rec / doc.name).write_bytes(text_pdf(printed))
        n += len(printed)

    def pages_of(doc: str, logical: list[int]) -> list[int]:
        return [p for k in logical for p in where[(doc, k)]]

    encounters = []
    for e in sorted(case.encounters, key=lambda x: (x["date"], pages_of(x["doc"], x["logical"])[0])):
        e = dict(e)
        e["pages"] = pages_of(e.pop("doc"), e.pop("logical"))
        encounters.append(e)
    dup_pages = [p for d, k in case.duplicates for p in where[(d, k)]]
    next(e for e in encounters if e["visit_type"] == "Emergency department visit")["duplicate_pages"] = dup_pages
    dates = sorted({e["date"] for e in encounters if not e["pre_incident"]})
    gaps = [{"start": a, "end": b, "days": (date.fromisoformat(b) - date.fromisoformat(a)).days}
            for a, b in zip(dates, dates[1:]) if (date.fromisoformat(b) - date.fromisoformat(a)).days > 30]
    truth = {
        "note": "Answer key for the synthetic case made by `python -m medchron sample`. Fictional patient.",
        "patient": PATIENT, "dob": DOB.isoformat(), "doi": DOI.isoformat(), "pages": n,
        "encounters": encounters, "gaps": gaps, "bills": case.bills,
        "total_billed": round(sum(b["charge"] for b in case.bills), 2),
        "no_content_pages": [p for d, k in case.no_content for p in where[(d, k)]],
        "other_patient_pages": [p for d, k in case.other_patient for p in where[(d, k)]],
    }
    (out / "truth.json").write_text(json.dumps(truth, indent=1))
    return truth


# --- scoring a run against the answer key -------------------------------------------------------------------------

def _codes(dx: list) -> set[str]:
    return {str(d.get("code", "") if isinstance(d, dict) else d).replace(".", "").upper() for d in dx} - {""}


def _matches(pred: dict, t: dict) -> bool:
    if pred.get("date") != t["date"]:
        return False
    pp = name_tokens(pred.get("provider", ""))
    if pp:
        return bool(pp & name_tokens(t["provider"]))
    return bool(name_tokens(pred.get("facility", "")) & name_tokens(t["facility"]))


def score(chron: dict, truth: dict) -> dict:
    pred = chron["entries"]
    used: set[int] = set()
    found, missed = [], []
    for t in truth["encounters"]:
        i = next((k for k, p in enumerate(pred) if k not in used and _matches(p, t)), None)
        if i is None:
            missed.append(t)
        else:
            used.add(i)
            found.append((t, pred[i]))
    extra = [p for k, p in enumerate(pred) if k not in used]
    truth_codes = sum(len(t["icd"]) for t in truth["encounters"])
    codes_found = sum(len({c.replace(".", "") for c in t["icd"]} & _codes(p["diagnoses"])) for t, p in found)
    wrong_codes = sum(len(_codes(p["diagnoses"]) - {c.replace(".", "") for c in t["icd"]}) for t, p in found)
    right_page = sum(1 for t, p in found if set(t["pages"]) & set(p.get("pages", [])))
    pre_ok = sum(1 for t, p in found if bool(p.get("pre_incident")) == t["pre_incident"])
    dup_truth = [t for t in truth["encounters"] if t.get("duplicate_pages")]
    dup_ok = sum(1 for t, p in found if t.get("duplicate_pages")
                 and set(t["duplicate_pages"]) & set(p.get("duplicate_pages", [])))
    pred_gaps = {(g["start"], g["end"]) for g in chron["gaps"]}
    truth_gaps = {(g["start"], g["end"]) for g in truth["gaps"]}
    tb = [(b["date"], b["code"], round(b["charge"], 2)) for b in truth["bills"]]
    pb = [(b.get("date"), b.get("code"), round(b["charge"], 2) if b.get("charge") is not None else None)
          for b in chron["bills"]]
    bills_found = sum(1 for b in tb if b in pb)
    return {
        "encounters": len(truth["encounters"]), "found": len(found), "missed": missed, "extra": extra,
        "right_page": right_page, "codes": truth_codes, "codes_found": codes_found, "wrong_codes": wrong_codes,
        "pre_incident_ok": pre_ok, "duplicates": len(dup_truth), "duplicates_merged": dup_ok,
        "gaps": len(truth_gaps), "gaps_found": len(truth_gaps & pred_gaps), "extra_gaps": sorted(pred_gaps - truth_gaps),
        "bill_lines": len(tb), "bill_lines_found": bills_found, "bill_lines_extra": max(0, len(pb) - bills_found),
        "total_billed": chron.get("total_billed", 0.0), "truth_total_billed": truth["total_billed"],
        "flagged": sum(1 for p in pred if p.get("needs_review")),
    }


def _pct(a: int, b: int) -> str:
    return f"{a} of {b}" + (f" ({100 * a / b:.0f}%)" if b else "")


def format_score(s: dict) -> str:
    lines = [f"Encounters found: {_pct(s['found'], s['encounters'])}"]
    lines += [f"  missed: {t['date']} {t['visit_type']} ({t['provider']})" for t in s["missed"]]
    if s["extra"]:
        lines.append(f"  extra entries not in the answer key: {len(s['extra'])}")
        lines += [f"    {p.get('date') or 'undated'} {p.get('visit_type', '')} ({p.get('provider', '')}) "
                  f"pages {p.get('pages')}" for p in s["extra"]]
    lines += [
        f"Found encounters citing a right page: {_pct(s['right_page'], s['found'])}",
        f"Diagnosis codes found: {_pct(s['codes_found'], s['codes'])}; codes not in the records: {s['wrong_codes']}",
        f"Before-injury marking right: {_pct(s['pre_incident_ok'], s['found'])}",
        f"Duplicate records merged: {_pct(s['duplicates_merged'], s['duplicates'])}",
        f"Treatment gaps found: {_pct(s['gaps_found'], s['gaps'])}; extra gaps: {len(s['extra_gaps'])}",
        f"Bill lines found: {_pct(s['bill_lines_found'], s['bill_lines'])}; extra lines: {s['bill_lines_extra']}",
        f"Total billed: ${s['total_billed']:,.2f} (answer key ${s['truth_total_billed']:,.2f})",
        f"Entries flagged for review: {s['flagged']}",
    ]
    return "\n".join(lines)
