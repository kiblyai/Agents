# medchron: medical-chronology agent

Builds a medical chronology from a personal-injury case's medical records: every visit in date order with its source page, treatment gaps, records from before the injury, and a bills summary. It is idea #3 from the business analysis in [`analysis/`](../analysis/), a service for plaintiff personal-injury firms at about $0.90 a page.

> **Status (2026-09-29):** built and tested offline on a synthetic case with a stand-in model. It has not run on a real model yet. It is for synthetic records only until the compliance steps below are done.

## What it does

1. **Reads the case file:** a folder of PDFs, in file-name order (put numbers in front of the names to set the order).
   - Every page gets the number it has in the combined PDF, and the chronology cites those numbers.
   - Pages with no text layer (scans) are listed for review. `--ocr` adds a text layer first with `ocrmypdf`, which runs on your machine.
2. **Extracts in batches** of up to 5 pages from one file per model request:
   - each visit: date, provider, facility, visit type, complaints, findings, diagnoses with ICD-10 codes, treatment, work status and source pages
   - every billed charge
3. **Checks every entry** (these rules are code, not model judgment):
   - It drops page numbers the model wasn't shown.
   - The date must be written on the cited pages. If a page labels its date of service ("Date of visit:", "DOS", "Exam date:"), the entry must use that date, so a date of injury from the history or a birth date can't slip in.
   - It removes ICD-10 and billing codes that aren't on the cited pages.
   - Numbers such as pain scores, doses and lifting limits, and every charge, must appear on the cited pages.
   - Provider and facility names must appear on the pages. So must the patient's surname, which catches pages misfiled from another patient's chart.
4. **Builds the chronology:**
   - merges a visit split across pages, and merges duplicate copies of a record from another file (shown as "copy: 30-31")
   - sorts by date and marks records from before the injury
   - finds treatment gaps over 30 days (`--gap-days`) and the days from injury to first treatment
   - totals bills per provider in code, dropping copies of the same bill from another file
5. **Builds the checking queue** (`review.csv`):
   - flagged entries and bill lines
   - pages with no text, pages no entry uses, and pages about another patient
   - a random 10% of the confident entries to spot-check

Outputs in `--out` (default `out/medchron`):

| File | What it holds |
|---|---|
| `chronology.docx` | The deliverable: summary, providers, chronology table, treatment gaps, bill totals. Rows to check are yellow. |
| `records_combined.pdf` | All records in one PDF, each page stamped "Page N of M", with a bookmark for every entry |
| `review.csv` | What to check, in order, with page numbers |
| `chronology.xlsx` | The same data as sheets (chronology, gaps, itemized bills, bill totals, review, every page's status) |
| `chronology.json` | Machine-readable, used by `score` |
| `run_summary.json` | Counts, requests, tokens, cost |

Model replies are cached in `out/.../cache/`. A run stopped by the daily free limit continues where it left off.

## Try it on the synthetic case

`examples/medchron/` holds a made-up case file: 31 pages in 7 PDFs for a fictional patient, Dana Whitfield. It covers:

- an emergency visit and CT scan after a rear-end collision
- primary care, including a back-pain visit a year before the injury
- 8 physical-therapy visits, an orthopedic consultation and an MRI
- an epidural injection after a 57-day gap in treatment
- a second copy of the emergency record from an insurer
- a page from another patient's chart filed by mistake
- itemized bills totaling $11,928.00

`truth.json` is the answer key, and `score` compares a run against it.

```bash
python -m medchron pages --records examples/medchron/records
python -m medchron run --records examples/medchron/records --patient "Dana Whitfield" --doi 2025-03-14 --out out/medchron/sample --synthetic
python -m medchron score --run out/medchron/sample
open out/medchron/sample/chronology.docx
```

The run takes about 9 model requests. With a perfect model, `score` shows 16 of 16 encounters, 1 of 1 gap and 25 of 25 bill lines.

To time your checking on a bigger file, make a longer case in `out/` (127 pages, about 28 requests) and score it against its own answer key:

```bash
python -m medchron sample --out out/medchron/big-case --pt-visits 100
python -m medchron run --records out/medchron/big-case/records --patient "Dana Whitfield" --doi 2025-03-14 --out out/medchron/big --synthetic
python -m medchron score --run out/medchron/big --truth out/medchron/big-case/truth.json
```

## Checking a chronology (and timing it)

1. Open `review.csv` next to `records_combined.pdf` and work top to bottom. Each row gives the pages to look at.
2. Fix the yellow rows in `chronology.docx`, then delete the "Check before sending" column and the DRAFT line.
3. Time it. The kill rule in `analysis/PLAN.md` is about 28 minutes per 100 pages; slower than that and $0.90 a page pays less than $101/h.

## Real client files: compliance first

- **Medical records are protected health information under HIPAA.** The tool refuses to run on anything but `--synthetic` records unless both of these hold:
  - the model is not a free one
  - `MEDCHRON_BAA=yes` is set, which you do only after the model provider has signed a business associate agreement (BAA) with you
- **Before the first real file,** do step 1 of section 3.3 in `analysis/PLAN.md`: AWS's BAA, a model confirmed as HIPAA-eligible, a BAA template for firms, and E&O insurance.
- **Model settings for real files:** `MEDCHRON_BASE_URL`, `MEDCHRON_API_KEY` and `MEDCHRON_MODEL` point to a BAA-covered endpoint. The client speaks the OpenAI-compatible chat API, so a provider without one needs an adapter first (see "Not built yet").
- **Keep files local.** OCR runs locally, and `out/` is git-ignored. Delete each case's folders after 30 days, as promised to firms.
- **Every chronology needs a person's check** before it goes to the firm.

## Costs and limits

- **Requests:** about 1 per 5 pages, so a 1,000-page file is about 200 requests.
- **Free models** (synthetic records only) allow 50 requests a day, or 1,000 after $10 of credit. That's about 250 pages a day, or 5,000 with credit.
- **Paid models:** each request is roughly 4-6k tokens. A 1,000-page file comes to about $5-10 on a mid-priced model, against about $900 of revenue.

## Not built yet

- Secure upload for firms (presigned S3) and a direct client for a BAA-covered model (for example Bedrock), steps 1-2 of the plan
- Handwritten notes: OCR reads them poorly. Such pages show up in the queue as "no entry or bill uses this page".
- Rebuilding the Word file from an edited Excel file; for now, edit the Word file directly
- Links from the Word file into the PDF; the PDF's bookmarks do this job
- Scan noise in the synthetic records, which are clean text PDFs
- Payments and adjustments on bills; only charges are totaled

## Tests

```bash
python -m pytest -q
```

The tests use a fake model that answers from the answer key, plus deliberate model mistakes the checks must catch. They need no network or API key.
