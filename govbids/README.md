# govbids: federal bid matching and RFP compliance matrices

Two jobs for a small federal contractor:
- **`match`:** every morning, find the SAM.gov notices worth pursuing.
- **`shred`:** when one is worth bidding, turn the RFP into a compliance matrix and a sheet of key facts.

It is idea #11 from the business analysis in [`analysis/`](../analysis/): software for contractors with 5-50 staff at about $149 a month.

> **Status (2026-10-11):** built and tested offline on synthetic notices and a synthetic RFP with a stand-in model. `fetch` was tested against a fake SAM.gov API only. It has not run on a real model or real notices yet.

## What it does

**`match`: which notices to pursue**
1. **Reads notices** from SAM.gov's Contract Opportunities CSV export, or from `fetch`, which downloads the last few days for your NAICS codes with a free SAM.gov API key.
2. **Filters them out in code** before any model request:
   - notice types you don't want (award notices, special notices)
   - NAICS codes that aren't yours
   - set-asides you don't qualify for (8(a), HUBZone, WOSB and so on, matched against your certifications)
   - deadlines already passed or closer than `min_days`
   - work in states you don't cover
   - words you never bid on
   A clearance above yours (TS/SCI when you hold Secret) is noted.
3. **Scores the rest, 5 per request:** fit 0-100, bid / respond / pass, why, matches to your capabilities, risks and questions for the contracting officer.
   - Every match and risk must quote the notice. Quotes that aren't there are dropped, and a fit with no quoted match is capped at 40.
   - A sources sought or presolicitation can't be marked "bid", only "respond".

**`shred`: the RFP into a compliance matrix**
1. **Finds every requirement in code:** each sentence with shall, must, will, is required and the like, kept word for word with its section number (C.3, L.4) and page. The model never rewrites the RFP.
2. **Labels them** (40 per request): type (technical, staffing, security, format, instruction, past performance, pricing, evaluation, and so on), the volume that must answer it, a short summary, and whether the offeror must act or it's something the Government will do. A sentence that reads as a Government action but was labeled as the offeror's is flagged.
3. **Pulls the key facts** (1 request): due date and time, questions deadline, submission method, page limits per volume, format, evaluation basis, contract type and period of performance. Each needs a quote from the RFP, and the date or page count must be the one in that quote.

## Outputs

| File | What it holds |
|---|---|
| `digest.md` | The morning read: notices to bid or respond to, with why, matches, risks and questions; then a count of the rest |
| `matches.csv` / `matches.xlsx` | Every notice with its status (bid, respond, pass, filtered out and why), fit, quotes, notes and link |
| `compliance_matrix.xlsx` / `.csv` | One row per requirement: ID, section, page, the RFP's words, type, volume, summary, offeror must or not, and empty columns for proposal section, owner and status |
| `key_facts.csv` | Due date, questions deadline, submission, page limits, format, evaluation, contract type, period, each with its quote |
| `review_shred.csv` | Labels and facts to check |
| `matches.json`, `shred.json` | Machine-readable results |

## Try it on the example

`examples/govbids/` holds a made-up contractor, "Cobalt Ridge Systems LLC" (SDVOSB, Secret clearance, IT services), plus:
- `notices.csv`: 12 made-up notices from a fictional agency. Seven should be filtered out by the rules (wrong NAICS, 8(a) and WOSB set-asides, due in 2 days, an award notice, work in Colorado), two should be bid, one is a sources sought to respond to, and two are passes: an engineering job outside IT, and a help desk that needs TS/SCI.
- `rfp/`: a 2-page made-up RFP with Sections C, L and M
- `truth.json`: the answer key

```bash
python -m govbids notices --company examples/govbids/company.toml --notices examples/govbids/notices.csv --today 2026-10-12
python -m govbids match --company examples/govbids/company.toml --notices examples/govbids/notices.csv --today 2026-10-12 --out out/govbids/sample
python -m govbids shred --rfp examples/govbids/rfp --out out/govbids/sample
python -m govbids score --run out/govbids/sample
open out/govbids/sample/digest.md
open out/govbids/sample/compliance_matrix.xlsx
```

`match` takes 1 model request (5 notices in one batch) and `shred` takes 2. With a perfect model, `score` shows 12 of 12 notices sorted right, all 7 filtered for the right reason, 20 of 20 requirements found and labeled right, and 9 of 9 key facts.

## Using it for a contractor

- **Profile:** copy `examples/govbids/company.toml`. Set the NAICS codes you're registered under in SAM.gov, the certifications you hold (SB, 8A, HZ, SDVOSB, VOSB, WOSB, EDWOSB), your capabilities in plain words, past performance, the states you work in and your clearance.
- **Every morning:** `fetch --days 1` then `match`, and read `digest.md`. The model assumes about 50 notices a day reach the scoring step for some contractors; the rules usually cut that a lot, and 5 go in each request.
- **When you bid:** run `shred` on the RFP and its attachments and add the compliance matrix to your proposal outline. Check every requirement against the RFP before relying on it, since sentences without "shall" or "must" can still be requirements.
- **SAM.gov API key:** free from your SAM.gov account (Account Details > API Key). Put it in `.env` as `SAM_API_KEY`.

## Model and privacy

- **Settings:** it uses the same `OPENROUTER_API_KEY` and model settings as leadagent. `GOVBIDS_MODEL`, `GOVBIDS_FALLBACK_MODELS`, `GOVBIDS_RPM` and `GOVBIDS_BASE_URL` override them.
- **Notices and public RFPs are public,** so free models are fine for them. Use a paid model with zero data retention for anything controlled (CUI) or a client's own proposal material, and never put classified material into any of this.
- **Cost:** about 1-2k tokens per notice scored, and 5-30k per RFP shredded; a few dollars a month at 50 notices a day on a cheap paid model.

## Not built yet

- Writing proposal sections (the analysis mentions drafting; only matching and shredding are built)
- Email delivery of the digest, and a web app with trials (the analysis models a SaaS with free trials)
- Attachments listed on SAM.gov (fetch downloads the description only; save RFP files yourself)
- Amendments: re-run `shred` on the amended RFP and compare the matrices

## Tests

```bash
python -m pytest -q tests/test_govbids.py
```

The tests use a fake model that answers from the answer key, wrong quotes, dates and page counts the checks must catch, and a fake SAM.gov API. They need no network or API key.
