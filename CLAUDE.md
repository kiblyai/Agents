# Working notes for Claude sessions

## What this repo is
AI-agent service businesses for a solo founder, built one agent at a time. Business case and rankings: `analysis/`
- `results.md`: unit-economics model output
- `PLAN.md`: launch plans, 30-day tests and kill rules
- `SOURCES.md`: sources

**Founder profile:** experienced in AI/ML, 10-20 h/week, values their time at $101/h, no audience, sells by cold email. Prefers short, plain-language answers with concrete next steps.

## Layout and status
| Folder | Status | Next step |
|---|---|---|
| `agentkit/` | Shared: OpenRouter model client (rate limit, retries, backup models, JSON repair, daily-limit stop), per-agent settings (`PREFIX_*` falling back to `LEADAGENT_*`), a cached request runner, document readers, BM25 search, CSV/Excel writers, shared checks (quotes, numbers, dates, emails), an email reader, US places, `.env` loading and a small PDF writer for synthetic examples | none |
| `leadagent/` | **Parked.** Company list in, scored and cited leads with opening lines out. Tuned over 3 real runs. | Test on about 20 real Apollo prospects; see `leadagent/README.md` |
| `secq/` | **Active.** Security-questionnaire drafting with citations and review flags. Tested on the fictional example with a stand-in model only. | Founder runs the example on their Mac and sends `review.csv` plus the draft xlsx; check answer quality, then fix |
| `medchron/` | **Active.** Medical chronology for personal-injury firms: PDFs in, cited chronology (Word), gaps, bills, review queue and a bookmarked combined PDF out. Tested offline on the synthetic case with a stand-in model only. | Founder runs the synthetic case on their Mac (`--synthetic`, free model) and sends the `score` output plus `review.csv`; check extraction quality, then fix |
| `leadactor/` | **Active.** leadagent as a pay-per-event Apify Store Actor (idea #4). Apify files in `.actor/`. Charges one `company-scored` event per scored company; unreachable sites and bad input are free. Tested offline only, with a stand-in model and the real Apify SDK billing locally. | Founder runs the example with a paid model (`leadactor/README.md` step 2) and sends the cost line plus `results.csv`; then publishes (step 3) |
| `freightq/` | **Active.** Freight-broker quotes (idea #6): shipper emails (.eml/.txt, PDF tenders) in; checked lanes, prices from the broker's own lane history (code only), draft replies, `review.csv` and TMS rows for tenders out. Tested offline on the synthetic inbox with a stand-in model only. | Founder runs the synthetic inbox on their Mac (free model) and sends the `score` output plus `review.csv`; check extraction quality, then fix |
| `outreach/` | **Active.** Pay-per-meeting cold email (idea #5): leads screened against the client's meeting bar in code; 3-email sequences checked in code (placeholders, unsupported numbers, banned phrases, links, footer); replies sorted, with bounces, opt-outs and away messages decided by rules; suppression list, follow-ups and the monthly invoice. Tested offline on a synthetic campaign with a stand-in model only. | Founder runs the synthetic campaign on their Mac (free model) and sends the `score` output plus `review_sequences.csv` and `triage.csv`; check writing quality, then fix |
| `shopifypo/` | **Active.** Wholesale POs into Shopify draft orders (idea #7): emailed POs (PDF, Excel, Word, CSV, email text) in; every line matched to the catalog and price list in code; checks on prices, case packs, stock, duplicates and addresses; `push` creates draft orders through the Shopify Admin API (dry run unless `--yes`). Tested offline with a stand-in model and a fake Shopify API only. | Founder runs the synthetic inbox (free model) and sends the `score` output plus `review.csv`; later, try `push` on a development store |
| `grants/` | **Active.** Grant prospecting and letters of inquiry for small nonprofits (idea #8): funder rules (invite-only, geography, grant size, deadline) in code; fit scored with quotes; the letter drafted from the nonprofit's own documents, with checks on numbers, the ask and placeholders. Tested offline on a synthetic nonprofit with a stand-in model only. | Founder runs the synthetic nonprofit (free model) and sends the `score` output plus `review_draft.csv` and the draft .docx |
| `txcoord/` | **Active.** Real-estate contract deadlines (idea #9): the model copies the contract's terms word for word; code works out every date (business days, federal holidays, addenda) and writes the timeline, calendar (.ics), checklist and emails to the parties. Tested offline on synthetic contracts with a stand-in model only. | Founder runs the synthetic contracts (free model) and sends the `score` output plus `review.csv` |
| `crmclean/` | **Active.** HubSpot cleanup and enrichment (idea #10): duplicates, formats, missing links and stale leads found by rules; company details from each company's own website, with quotes; nothing changes until a person approves (`apply` writes import files, or calls the HubSpot API with `--yes`). Tested offline with a stand-in model and a fake HubSpot API only. | Founder runs the synthetic portal (free model) and sends the `score` output plus `changes.csv` |
| `govbids/` | **Active.** Federal bid matching and RFP shredding (idea #11): SAM.gov notices filtered in code (NAICS, set-asides, deadline, place) and scored 5 per request with quotes; RFP requirements found in code word for word and labeled by the model into a compliance matrix. Tested offline with a stand-in model and a fake SAM.gov API only. | Founder runs the synthetic notices and RFP (free model) and sends the `score` output plus `review_shred.csv` |
| `upworkbid/` | **Active.** Upwork job triage and proposals (idea #12): job pages saved as text in; the budget, client record, competition and off-platform requests checked in code; fixed budgets turned into $/h after fees from the founder's own hours; proposal drafts checked for invented numbers, contact details and missing answers. Never submits anything (Upwork's terms). Tested offline on synthetic posts with a stand-in model only. | Founder runs the synthetic posts (free model) and sends the `score` output; then a real day's feed (job posts are public, so free models are fine) |
| `examples/` | Sample inputs, all fictional: companies and an ICP for leadagent and leadactor; a company ("Northwind Analytics") for secq; and for medchron, freightq, outreach, shopifypo, grants, txcoord, crmclean, govbids and upworkbid, a synthetic case with an answer key (`examples/<agent>/truth.json`) that `python -m <agent> score` compares a run with. Regenerate those with `python -m <agent> sample`, never by hand (tests compare them byte for byte). | none |
| `tests/` | Run offline with fakes (`tests/fakes.py`): `python -m pytest -q` | none |

## Rules we agreed
- **Keys:** `OPENROUTER_API_KEY` lives in environment variables or the git-ignored `.env`. Never write it to code, specs, output or chat. The same goes for `SHOPIFY_ACCESS_TOKEN` (shopifypo), `HUBSPOT_ACCESS_TOKEN` (crmclean) and `SAM_API_KEY` (govbids). The founder once pasted a key into `.env.example` on GitHub; secret scanning blocked the commit. Watch for this.
- **Models (via OpenRouter):** main `nvidia/nemotron-3-super-120b-a12b:free`, backup `google/gemma-4-31b-it:free`. Free models are shared and often busy, capped at 20 requests/min and 50/day (1,000/day after $10 of credit). The agents stop cleanly at the daily limit and resume from cache. Paid Apify runs (leadactor) refuse free models.
- **Privacy:** free models may log prompts. Use them only with public or example data, never a client's real documents. For client work, use a paid model with zero data retention.
- **Medical records (medchron):** real records are HIPAA-protected. Only synthetic records until the founder has a BAA with the model provider (`analysis/PLAN.md` 3.3 step 1). The code enforces it: a run needs `--synthetic`, or a paid model plus `MEDCHRON_BAA=yes`. Never weaken this check.
- **Checks in code:** the model drafts, and code checks citations, numbers, dates and hard criteria. When a real run shows a bad output, add a regression test named after the real case (e.g. "the Supabase case") and fix the code.
- **Git:** develop on the branch the session names. Bump `CACHE_VERSION` in an agent's `pipeline.py` when its research or drafting logic changes.

## Founder's machine (they run everything locally and paste results back)
- Mac mini, zsh, repo at `~/Agents`, venv at `.venv`, Python 3.12 from Homebrew.
- zsh doesn't accept `#` comments in pasted commands, so give commands without inline comments.
- After pulling, `pip install -e ".[dev]"` may be needed when dependencies change.

## Cloud-session gotchas
- This container can't reach openrouter.ai or ordinary websites (network policy). Test with fakes or a local stand-in server on 127.0.0.1.
- `pkill -f <pattern>` kills your own shell if the pattern appears in the same command line. Use a pattern that doesn't.
- If `pypdf` fails to import (broken system `cryptography`): `pip install --ignore-installed cffi cryptography`.
