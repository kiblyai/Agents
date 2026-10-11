# upworkbid: which Upwork jobs to bid on, with checked proposals

For a freelance AI engineer on Upwork. Paste in job posts and get back:
- a ranked list of which jobs to bid on, with the money worked out in code
- a proposal draft for each one worth bidding on

It is idea #12 from the business analysis in [`analysis/`](../analysis/): custom agent builds through Upwork, about $2.5k a project. The kill rule: fewer than 2 interviews from the first 40 proposals, or no offer that works out at $90/h or more after fees.

> **Status (2026-10-11):** built and tested offline on 8 synthetic job posts with a stand-in model. It has not run on a real model or real job posts yet.

## What it does

1. **Reads each job post:** the text of the Upwork job page, saved as a .txt file. Code reads:
   - the budget: a fixed price, or an hourly range
   - proposals so far and the Connects needed to bid
   - the client's record: payment verified, rating, hire rate, jobs posted, total spent, member since
   - skill tags and screening questions
2. **Skips jobs in code,** before any model request:
   - payment method not verified
   - hourly jobs whose top rate, after Upwork's fee, is under your floor ($90/h)
   - fixed budgets under your minimum
   - more proposals already than you allow
   - clients who hire on under 30% of 5 or more jobs
   - posts asking you to talk or get paid outside Upwork. This is a common scam and against Upwork's rules. A job to *build* a WhatsApp or Telegram bot doesn't count.
   - words you never take on (data entry, WordPress and so on)
   Requests for unpaid test work are noted.
3. **Assesses the rest, 1 model request per job:**
   - project type, picked from your own list
   - fit 0-100
   - requirements and red flags, each quoted word for word from the post
   - a proposal and answers to the screening questions
4. **Decides the money in code.** For a fixed price, your own hours for that project type turn the budget into an hourly rate after fees:
   - **skip** if the rate is under your floor at the middle of your hours estimate, or fit is under 40
   - **note** if it's under your floor only at the top of your estimate (scope it tightly)
   - **maybe** if the client is weak, fit is 40-59, under 2 skills match, or the draft needs fixing
5. **Checks every draft:**
   - it opens with a detail copied from the post
   - it names a portfolio item you really have
   - it uses no numbers that aren't in your profile or the post (no "10 years" when the post only says "10 to 15 proposals")
   - it has no email, phone or outside links, which break Upwork's rules before a contract, and none of the banned phrases ("Dear Hiring Manager" and so on)
   - it has no placeholders and stays under the word limit
   - it has one answer per screening question
6. **Ranks** the jobs to bid on, then the maybes, by fit, client score, competition and rate.

It never logs in to Upwork or sends anything, because Upwork's terms don't allow automated access. You save the pages and send each proposal yourself.

## Outputs

| File | What it holds |
|---|---|
| `jobs.csv` / `jobs.xlsx` | Every job, ranked, with: decision and reasons, budget, rate after fees and worst case, your hours estimate, client score and notes, proposals, Connects, fit, quoted requirements and red flags, and draft checks |
| `proposals/*.txt` | One per job to bid on or maybe: the cover letter and screening answers to paste into Upwork, plus anything to fix before sending |
| `jobs.json`, `run_summary.json` | Machine-readable results, Connects and model cost |

## Try it on the example

`examples/upworkbid/` holds a made-up freelancer, "Sam Okafor" (`profile.toml`), and 8 made-up job posts:
- **4 the rules skip:** a chatbot paying $25-40/h, a $50 job asking for Telegram, a client with unverified payment, and a job with 50+ proposals
- **3 to bid on:** a HubSpot lead agent ($3,000 fixed), an HR-policy assistant ($80-120/h), and invoice PDF extraction ($2,000 fixed: $90/h, or $72/h if it runs long)
- **1 maybe:** a news digest for a brand-new client with no record
- **`truth.json`:** the answer key

```bash
python -m upworkbid jobs --profile examples/upworkbid/profile.toml --jobs examples/upworkbid/jobs --today 2026-10-12
python -m upworkbid run --profile examples/upworkbid/profile.toml --jobs examples/upworkbid/jobs --today 2026-10-12 --out out/upworkbid/sample
python -m upworkbid score --run out/upworkbid/sample
open out/upworkbid/sample/jobs.xlsx
open out/upworkbid/sample/proposals
```

`jobs` makes no model requests. `run` takes 4, because the rules skip the other 4 posts. With a perfect model, `score` shows:
- 8 of 8 jobs decided right
- all 4 skipped for the right reason
- 4 of 4 rates right
- 3 of 3 proposals ready to send

Bidding on those 3 costs 42 Connects (about $6.30).

## Using it for real

- **Profile:** copy `examples/upworkbid/profile.toml` and set:
  - your rate, floor after fees ($90/h, the kill rule) and minimum fixed budget
  - `fee`: Upwork's fee as shown on your proposal page (10% by default)
  - `max_proposals`: the most proposals a job may already have
  - skills, as they appear in Upwork's tags
  - portfolio items: real work only, with what each one shows. Proposals may only use numbers that appear here or in the post.
  - project types with honest hours ranges. These turn a fixed budget into an hourly rate, so they decide most bids.
- **Each day:**
  1. Make a folder for the day: `mkdir -p out/upwork/jobs` (`out/` stays out of git).
  2. Open the promising jobs in your Upwork feed. On each job page press Cmd+A, then Cmd+C, and save the text with `pbpaste > out/upwork/jobs/job01.txt` (one file per job).
  3. Run `jobs` with `--profile` pointing at your profile and `--jobs out/upwork/jobs`, and check that each budget and client was read right.
  4. Run `run` with the same options, then read and edit each proposal and send it from Upwork yourself.
- **The 30-day test:** keep a simple sheet of proposals sent, replies, interviews and offers. Stop if 40 proposals bring fewer than 2 interviews, or no offer reaches $90/h after fees.

## Model and privacy

- **Settings:** it uses the same `OPENROUTER_API_KEY` and model settings as leadagent. `UPWORKBID_MODEL`, `UPWORKBID_FALLBACK_MODELS`, `UPWORKBID_RPM` and `UPWORKBID_BASE_URL` override them.
- **Job posts are public,** so free models are fine. Your profile goes into every request too, so keep anything private out of it.
- **Cost:** about 2-3k tokens per job assessed. On free models each job is 1 of your 50 requests a day (1,000 a day after $10 of credit).

## Not built yet

- Fetching jobs automatically: Upwork's API needs an approved key and scraping breaks its terms, so you save the pages yourself.
- Tracking replies, interviews and win rate for the kill rule. Use a sheet for now.
- Choosing your hourly bid. It checks the client's top rate against your floor; you pick the rate.

## Tests

```bash
python -m pytest -q tests/test_upworkbid.py
```

The tests use:
- a fake model that answers from the answer key
- drafts with invented claims, contact details, outside links and missing answers that the checks must catch
- a job page laid out as Upwork shows it

They need no network or API key.
