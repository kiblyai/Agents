# Sections 3-4: launch plans for the top 3, and the assumptions that move the ranking

All figures come from `model.py` (base case) and `thresholds.py`. The tables are **[Estimate]** outputs; the inputs are labeled in `model.py`.

## Setup shared by all three

- **Sending setup, day 1.** Buy 4 domains (~$48) and set up 12 Google Workspace inboxes (~$101/mo), plus Instantly ($37) and Apollo Basic ($59). The model assumed 6 inboxes; 12 costs ~$50/mo more (under $1k a year). You need it so you can still reach ~1,000 new contacts in the first 30 days even though new inboxes need about 14 days of warm-up. Until warm-up ends, hand-send up to ~10 emails a day from your main domain. Never send in bulk from your main domain.
- **Compliance.** Every email carries your postal address and a one-line opt-out (US anti-spam law, CAN-SPAM). For UK/EU contacts, rely on "legitimate interest" and honour opt-outs immediately. [Assumption; this is not legal advice.]
- **Build order.** The lead-list agent (idea 1) is also the prospecting engine for ideas 2 and 3, so build it first whichever offer wins.
- **Reading a kill signal.** Reply counts from 1,000 contacts are noisy. If the true positive-reply rate is the base 0.5%, you will still see 2 or fewer positive replies 12% of the time. If it is the conservative 0.3%, you will see 2 or fewer 42% of the time [Poisson estimate]. Use a three-way rule per 1,000 contacts:
  - 2 or fewer positive replies: kill.
  - 3-4: send another 1,000 before deciding.
  - 5 or more: proceed.

---

## 3.1 Lead-list retainer (#1 overall)

### 12-month cash flow (base case)

| Month | New customers | Active | Revenue | Cash costs | Your hours | Cash net | Cumulative cash | Cumulative true profit |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.00 | 0.0 | $0 | $322 | 47 | -$322 | -$322 | -$5.1k |
| 2 | 0.67 | 0.7 | $675 | $311 | 26 | $364 | $42 | -$7.3k |
| 3 | 1.12 | 1.7 | $1.7k | $508 | 34 | $1.2k | $1.3k | -$9.6k |
| 4 | 1.12 | 2.7 | $2.7k | $685 | 37 | $2.0k | $3.3k | -$11k |
| 5 | 1.12 | 3.5 | $3.5k | $844 | 40 | $2.7k | $6.0k | -$13k |
| 6 | 1.12 | 4.3 | $4.3k | $987 | 42 | $3.3k | $9.3k | -$14k |
| 7 | 1.12 | 5.0 | $5.0k | $1.1k | 44 | $3.9k | $13k | -$14k |
| 8 | 1.12 | 5.6 | $5.6k | $1.2k | 46 | $4.4k | $18k | -$14k |
| 9 | 1.12 | 6.2 | $6.2k | $1.3k | 48 | $4.9k | $22k | -$14k |
| 10 | 1.12 | 6.7 | $6.7k | $1.4k | 49 | $5.3k | $28k | -$14k |
| 11 | 1.12 | 7.2 | $7.2k | $1.5k | 51 | $5.6k | $33k | -$14k |
| 12 | 1.12 | 7.6 | $7.6k | $1.6k | 52 | $6.0k | $39k | -$13k |

True profit turns at month 21; month-12 run-rate is $115/h.

### First customers

- **Who.** B2B agencies and consultancies with 5-50 staff that live on new clients (marketing, dev, RevOps, fractional-exec firms), in the US, UK, Canada and Australia. Title: founder or head of growth. List source: Apollo filters, then your agent checks each website for a clear ideal-customer profile ("ICP").
- **Offer.**
  - Monthly: 2,000 contacts that fit their ICP, each with a verified email, an ICP score, and a one-line "why now" signal (hiring, funding, new product, tech change). Delivered as CSV or into their CRM or sequencer. $1,000/mo, month-to-month.
  - Paid pilot: 500 leads for $250, credited to month one.
  - Free sample: 25 leads.
- **Data rights.** Check your data vendor's terms on passing contact data to clients before the first sale. If they are unclear, have the client connect their own Apollo or Clay account and sell the research and scoring on top.

**Email 1.** Subject: `3 {their ICP} for {Company}`

> Hi {First} - I build research agents for outbound. I pointed one at what looks like {Company}'s ideal client ({ICP guess, e.g. "Series A fintechs hiring their first marketer"}). Three it found this week:
> - {Company A}: {signal}
> - {Company B}: {signal}
> - {Company C}: {signal}
>
> Want the other 22, with verified emails? Free, no call needed. If they're useful, I deliver 2,000 like this a month for $1,000.

**Follow-ups.**
- Day 3: "Should I send the 22? If I've got your ICP wrong, tell me in one line and I'll rerun it."
- Day 8: "Closing the loop. If outbound isn't a priority this quarter, no problem."

The email is itself the product demo. Each one costs about $0.15 in research [Estimate].

**Weekly targets (from week 3).**

| Activity | Target |
|---|---|
| New contacts | 375 |
| Reply time | Within 4 business hours |
| Positive replies | 2 or more |
| Free samples delivered (within 24 h) | 1 or more |
| Calls | 1 or more |
| Paid | 1 pilot per month |
| Your time | ~8 h on outreach and calls |

### First 2 weeks: build (~35 h)

1. ICP spec to candidate companies: Apollo export or public lists (4 h).
2. Research agent per company: web search plus fetching the homepage, careers and news pages, then Haiku extracts signals and scores ICP fit against a rubric as structured JSON (10 h).
3. Contact finding and email verification through a pay-per-use API (4 h).
4. First-line writer using Sonnet, with a banned-phrases list (3 h).
5. Output: Google Sheet or CSV, plus a CSV import format for Instantly and HubSpot (3 h).
6. Quality check: hand-check 5% of each batch. Target 90% or more deliverable emails and 80% or more correct signals (4 h).
7. Selling assets: one-page site, a sample list, Stripe payment links ($250 pilot, $1,000/mo), and a one-page terms document (7 h).

**Don't build:** a web app, self-serve signup, CRM integrations beyond CSV, or dashboards.

### 30-day validation

| Days | What to do |
|---|---|
| 1-14 | Build; warm up inboxes; hand-send 50-100 sample-first emails from your main domain. |
| 15-30 | Send to 1,000 or more new contacts. |

**Measure:**
- positive replies per 1,000 contacts
- samples requested and delivered
- calls held
- paid pilots
- your quality-check minutes per 500 leads

**Kill criteria:**
- 2 or fewer positive replies per 1,000 contacts: kill.
- 3 or more positive replies but no paid pilot by day 30: change one thing (ICP or price) and re-test for 2 weeks. Kill at day 45 if there is still no paid pilot.
- Your checking time runs above ~4 h per client-month (the model's line where you drop below $101/h): fix the pipeline before scaling.

---

## 3.2 Security-questionnaire service (#2 overall)

### 12-month cash flow (base case)

| Month | New customers | Active | Revenue | Cash costs | Your hours | Cash net | Cumulative cash | Cumulative true profit |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.00 | 0.0 | $0 | $397 | 57 | -$397 | -$397 | -$6.2k |
| 2 | 0.67 | 0.7 | $506 | $288 | 26 | $218 | -$179 | -$8.6k |
| 3 | 1.12 | 1.7 | $1.3k | $332 | 34 | $978 | $799 | -$11k |
| 4 | 1.12 | 2.7 | $2.0k | $372 | 37 | $1.7k | $2.5k | -$13k |
| 5 | 1.12 | 3.6 | $2.7k | $408 | 39 | $2.3k | $4.8k | -$15k |
| 6 | 1.12 | 4.5 | $3.4k | $442 | 40 | $2.9k | $7.7k | -$16k |
| 7 | 1.12 | 5.2 | $3.9k | $473 | 42 | $3.5k | $11k | -$17k |
| 8 | 1.12 | 5.9 | $4.5k | $502 | 44 | $4.0k | $15k | -$17k |
| 9 | 1.12 | 6.6 | $4.9k | $528 | 45 | $4.4k | $20k | -$17k |
| 10 | 1.12 | 7.2 | $5.4k | $553 | 47 | $4.8k | $24k | -$17k |
| 11 | 1.12 | 7.7 | $5.8k | $575 | 48 | $5.2k | $30k | -$17k |
| 12 | 1.12 | 8.2 | $6.2k | $595 | 49 | $5.6k | $35k | -$16k |

True profit turns at month 22; month-12 run-rate is $114/h.

### First customers

- **Who.** B2B SaaS companies with 10-150 staff, seed to Series B, selling to mid-market or enterprise. Signals: a trust or security page, "SOC 2" on the site, open roles for an enterprise account executive or solutions engineer, a raise in the last 18 months. Titles: CTO or co-founder, VP Engineering, head of security, head of RevOps.
- **Offer.**
  - Forward the questionnaire (Excel, Word or portal export) plus your existing documents. Within 48 hours you get it back filled in: every answer cites its source document, and answers that need the customer's own decision are flagged.
  - $500 per questionnaire (up to 300 questions); the first one is $250. The customer reviews and submits.
  - Portals such as OneTrust and Whistic are $150 extra, because you paste answers in by hand.
- **Second channel (week 3).** Email 30 virtual-CISO and SOC 2 consultancies offering white-label drafting at $350 per questionnaire. One partner can bring several clients.

**Email 1.** Subject: `{Company}'s next security questionnaire`

> Hi {First} - saw {Company} is {signal: "selling to hospitals" / "hiring an enterprise AE" / "SOC 2 Type II"}. Enterprise deals usually bring a 150-300-question security questionnaire, and it tends to land on the CTO for a day or two.
>
> I run a small service: you forward the questionnaire and your existing docs (SOC 2 report, policies, past answers). Within 48 hours you get it back filled in, every answer citing its source, and the few that need your call flagged. $500 per questionnaire; the first is $250.
>
> Want to try it on the next one that comes in?

**Follow-ups.**
- Day 3: a redacted 10-row sample showing the answers and their citations.
- Day 8: "If questionnaires go to someone else on your team, who should I ask?"

**Weekly targets (from week 3).**

| Activity | Target |
|---|---|
| New contacts | 375 |
| Positive replies | 2 or more |
| Calls | 1 or more |
| Consultancy emails | 10 |
| Paid questionnaire | 1 in the first 30 days |
| Your time | ~8 h on outreach and calls |

### First 2 weeks: build (~40 h)

1. **Document ingestion (5 h).** Convert PDF and DOCX to text. A startup's whole document set usually fits in one long, cached prompt, so skip the vector database. Cached reads on Sonnet cost $0.20 per million tokens [Sourced].
2. **Questionnaire input and output (10 h).** Parse Excel, CSV and Word tables. The model proposes which columns hold questions and answers; you confirm. Write answers back into the customer's original file with its formatting intact. This is what makes the service feel done for them.
3. **Drafting agent (8 h).** Batch about 20 questions per call. Each answer returns structured JSON: answer, explanation, citation (document and section), confidence, and a "needs customer decision" flag.
4. **Consistency pass (3 h).** Haiku checks for contradictions, e.g. "encryption at rest" answered two different ways.
5. **Approved-answer library per client (3 h).** SQLite, reused on the next questionnaire.
6. **Accuracy test (6 h).** Run two public questionnaires, CSA's CAIQ and EDUCAUSE's HECVAT Lite, against one public company's trust-center documents. Time your own checking.
7. **Business setup (5 h).** Stripe payment links; a mutual NDA; a one-page data-handling note (encryption, deletion after 30 days; API data isn't used for training, but verify Anthropic's commercial terms); get an E&O insurance quote before the first paid job.

**Don't build:** portal automation, a trust center, a web app, or self-serve.

### 30-day validation

| Days | What to do |
|---|---|
| 1-14 | Build and run the public-questionnaire test; warm up inboxes; hand-send 50 emails. |
| 15-30 | Send to 1,000 or more new contacts; email the consultancies. |

**Measure:**
- positive replies per 1,000 contacts
- calls held
- paid questionnaires
- your checking time per 250 questions
- accuracy (share of answers you had to change)

**Kill criteria:**
- 2 or fewer positive replies per 1,000 contacts and no paid questionnaire (even at $250) by day 30: kill.
- On the public test, checking takes more than ~2 h per questionnaire: at $500 you are below $101/h (the model's line is 3.0 h per client-month at 1.5 questionnaires). Get it under 2 h, or raise the price before selling: $550 or more at 2.5 h per questionnaire, $600 or more at 3 h.

---

## 3.3 Medical-chronology service (#3 overall)

### 12-month cash flow (base case)

| Month | New customers | Active | Revenue | Cash costs | Your hours | Cash net | Cumulative cash | Cumulative true profit |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.00 | 0.0 | $0 | $465 | 67 | -$465 | -$465 | -$7.2k |
| 2 | 0.76 | 0.8 | $816 | $333 | 31 | $484 | $19 | -$9.9k |
| 3 | 1.26 | 2.0 | $2.1k | $420 | 45 | $1.7k | $1.7k | -$13k |
| 4 | 1.26 | 3.1 | $3.4k | $501 | 52 | $2.9k | $4.6k | -$15k |
| 5 | 1.26 | 4.2 | $4.5k | $578 | 58 | $3.9k | $8.5k | -$17k |
| 6 | 1.26 | 5.2 | $5.6k | $650 | 64 | $5.0k | $13k | -$18k |
| 7 | 1.26 | 6.1 | $6.6k | $718 | 69 | $5.9k | $19k | -$20k |
| 8 | 1.26 | 7.0 | $7.6k | $782 | 74 | $6.8k | $26k | -$20k |
| 9 | 1.26 | 7.9 | $8.5k | $842 | 79 | $7.7k | $34k | -$21k |
| 10 | 1.26 | 8.7 | $9.4k | $898 | 84 | $8.5k | $42k | -$21k |
| 11 | 1.18 | 9.3 | $10k | $945 | 87 (cap) | $9.1k | $51k | -$20k |
| 12 | 0.98 | 9.7 | $11k | $975 | 87 (cap) | $9.5k | $61k | -$19k |

It hits the 20 h/wk cap in month 11. True profit turns at month 26; month-12 run-rate is $110/h. With a $35/h checker (section 4), true profit turns at month 13 instead.

### First customers

- **Who.** US plaintiff personal-injury firms with 1-20 attorneys. Titles: managing partner, case manager, lead litigation paralegal. List source: Apollo (law practices with personal-injury keywords) or state bar directories; the agent confirms personal-injury focus from each firm's website.
- **Offer.**
  - One real case file (up to 1,000 pages) done free.
  - After that, $0.90 a page with no minimum and no contract; outside services typically charge $1.50-$4.00 a page [Sourced].
  - Deliverable in 48 hours: a hyperlinked chronology (date, provider, visit type, complaints, diagnoses and ICD codes, treatment, work restrictions, source page) plus a treatment-gap report and a medical-bills summary.
  - You sign an NDA or business associate agreement (BAA, the HIPAA confidentiality contract), and files are deleted after 30 days.

**Email 1.** Subject: `free chronology for {Firm}'s next file`

> Hi {First} - quick question about medical records at {Firm}: do your paralegals build the chronologies, or do you send them out? Outside services usually charge $1.50-$4 a page.
>
> I run an AI-assisted chronology service: every entry cites its source page, a person checks it, turnaround is 48 hours, and it's $0.90 a page. I'll sign a BAA, and files are deleted after 30 days.
>
> Send one real file (up to 1,000 pages) and I'll do it free so you can compare it with your last one. Reply "file" and I'll send a secure upload link.

**Follow-ups.**
- Day 3: a sample chronology page built from synthetic records.
- Day 8: "Who handles medical records for your cases?"

**Weekly targets (from week 3).**

| Activity | Target |
|---|---|
| New contacts | 375 |
| Positive replies | 1.5 or more |
| Free pilots | Cap at 2 a week (each is ~3 h of checking) |
| Follow-up after each pilot | Within 5 days, asking for the next file at the paid rate |
| Your time | ~12 h on outreach, pilots and calls |

### First 2 weeks: build (~40 of the model's ~60 h; finish in weeks 3-4)

1. **Compliance first (4 h).**
   - Sign AWS's BAA through AWS Artifact.
   - Confirm that the exact Claude model you will call on Bedrock is on AWS's HIPAA-eligible list. Third-party guides say eligibility is set per model, so don't assume.
   - Prepare a BAA template for firms and buy E&O insurance.
   - Use only synthetic records until this step is done.
2. **Secure intake (3 h).** Presigned S3 upload with encryption and a folder per client.
3. **Text extraction with page mapping (6 h).** AWS Textract, or Claude vision per page; keep the original page numbers.
4. **Per-page extraction agent (10 h).** Fixed schema and a page citation on every field.
5. **Merge encounters, sort, detect gaps (6 h).** Duplicate records are common; flag treatment gaps over 30 days.
6. **Output (6 h).** Word or Excel chronology linking to a bookmarked combined PDF, plus a one-page summary.
7. **Checking queue (3 h).** Low-confidence entries, date conflicts, and a random 10% sample for you to check.
8. **Test set (2 h).** About 300 pages of synthetic records made with the LLM and rendered to PDF with scan noise.

**Don't build:** case-management integrations (Filevine, Clio), demand letters, or a client portal.

### 30-day validation

| Days | What to do |
|---|---|
| 1-14 | Compliance and the pipeline; time your checking on the synthetic set; warm up inboxes. |
| 15-30 | Send to 1,500 contacts; deliver the first pilots. |

**Measure:**
- pilots accepted
- checking minutes per 100 pages
- the firm's own verdict next to their last chronology
- pilots that become paid files

**Kill criteria:**
- 1 or fewer firms accept a pilot from 1,500 contacts.
- Checking takes more than ~28 minutes per 100 pages. At $0.90 a page that drops you below $101/h; the model's break line is 6.65 h per firm-month at 1,200 pages.
- None of the first 3 pilots sends a paid file within 3 weeks of delivery.

This is the most fragile of the three: the close rate only needs to fall from 35% to 30%, or checking time to rise 15%, for it to drop below $101/h. It also has the highest upside once a contractor does the checking.

---

## How I'd run them together within 20 h/week

1. **Days 1-14.** Build the lead-list agent (about 30 h). Set up the 12 inboxes. Use the agent to build three prospect lists of about 1,000 contacts each (agencies, SaaS, law firms).
2. **Days 15-30.** Send the lead-list offer and the questionnaire offer, about 1,000 contacts each. Build the questionnaire pipeline only when the first positive reply arrives; its 48-hour turnaround gives you time. Hold off on medical chronology: it needs about 60 h of build plus compliance work before it can take a real file.
3. **Days 31-60.** Decide using the kill rules. Keep any offer with 5 or more positive replies per 1,000 contacts and at least one paid job. If either offer died, start medical chronology in its slot.

---

## 4. Assumptions that most change the ranking

Ranked by how much each moves the result. Each "break line" is the value at which the idea's month-12 run-rate drops to $101/h, with everything else at base.

| # | Assumption | Base value | Break line (lists / questionnaires / chronology) | What it does |
|---|---|---|---|---|
| 1 | Your close rate on calls | 25% / 25% / 35% [Estimate] | 19.7% / 20.3% / 30.0% | The biggest risk, given that selling is not your strength. Halving it cuts 12-month true profit by $10-14k per idea and pushes true break-even past 36 months. |
| 2 | Hours you spend checking or delivering per customer | 3.0 / 2.25 / 5.8 h a month | 3.95 / 3.03 / 6.65 h | Chronology has only 15% headroom. Measure it on the test set before selling. |
| 3 | Paying a $35/h contractor for 75% of the checking | Not in the base case | n/a | The largest upside lever. Chronology's true break-even moves from month 26 to month 13 and its run-rate from $110 to $173/h, which would make it #1. Lead lists: month 21 to 15. Questionnaires: month 22 to 17. Cash stays under $500 before revenue. The catch is quality control, and for chronology the contractor must sign your BAA. |
| 4 | Positive-reply rate per contact | 0.5% / 0.5% / 0.4% [anchored on ~0.48%, Sourced] | 0.37% / 0.38% / 0.31% | A rate 22-26% below base drops all three below $101/h at month 12. Your first 1,000 contacts measure this directly. |
| 5 | What you earn per customer each month | $1,000 / $750 (1.5 questionnaires) / $1,080 (1,200 pages) | $901 / $669 / $996 | Questionnaire volume per customer is the least certain: it is lumpy and unverified. If customers average 1 a month, questionnaires fall to #3. |
| 6 | Value of your time | $101/h | n/a | At $50/h the order becomes lists, questionnaires, chronology, Upwork, grants, transaction coordination. At $101/h, Upwork is only worth it if you can charge about $4k per 24-hour project: that is the only case with positive year-1 true profit (+$1.8k, $110/h at month 12). |
| 7 | Monthly churn | 10% / 8% / 6% | 15% / 12% / 9% | Matters less inside 12 months; it matters more for LTV. |
| 8 | Token and data costs | Tokens under 5% of revenue (except lists: ~15% data) | n/a | Tripling all variable costs leaves the top 3 unchanged. Model prices are not your risk. |
| 9 | Ranking weights | Downside and confidence count 30% | n/a | Ranking on upside alone (optimistic-case true profit) gives the same top 3: lists +$91k, questionnaires +$83k, chronology +$62k, then freight +$50k and meetings +$35k. |

To re-run with your numbers: edit `pos_rate`, `close_rate`, `arpu`, `churn` and `delivery_h` in `analysis/model.py` (or `HOURLY` / `WEIGHTS`), then run `python3 analysis/model.py && python3 analysis/thresholds.py`. Or send me the corrected values.
