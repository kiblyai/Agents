# outreach: cold-email agent for the pay-per-meeting service

Runs the email side of an outbound meetings service for one client at a time:
- writes each lead's email sequence
- sorts the replies and drafts the answers
- lists who must never be emailed again
- works out which meetings the client pays for

It is idea #5 from the business analysis in [`analysis/`](../analysis/): B2B service firms (MSPs, agencies, consultancies) pay about $500 a month plus $250 per held meeting.

> **Status (2026-10-11):** built and tested offline on a synthetic campaign with a stand-in model. It has not run on a real model yet.

## What it does

1. **Screens the leads in code** before any model request. Leads can come from leadagent's `leads.csv`, an Apollo export or any CSV/Excel file with the usual columns. A lead is held back when:
   - it has no valid email, or repeats an earlier row
   - it is on the do-not-contact list (`--suppress`, e.g. the last run's `suppress.csv`)
   - it is outside the client's meeting bar (job title, company size, country). No email goes to someone the client wouldn't pay for.
2. **Writes a 3-email sequence per lead** (1 request per lead). Email 1 opens with one fact about the lead's company, preferably leadagent's recent signal. Follow-ups add one new reason to talk.
3. **Checks every email** (these rules are code, not model judgment). An email is held for a person to fix when it:
   - opens with a "fact" that isn't in the lead's data
   - states a number, result or client that isn't in the client's offer or proof list (e.g. "cut tickets 52%" when the proof says 38%)
   - has a link other than the client's booking link or website, or any link in email 1 (that hurts deliverability)
   - has a fake "Re:" or "Fwd:" subject on a first email (deceptive under US anti-spam law)
   - uses a banned phrase ("hope this email finds you well", "guarantee", ...), has an unfilled `{placeholder}`, or runs far over its word limit
   It also notes follow-ups that repeat email 1, and a first email that doesn't use the opening fact. Code appends the sender's name, the postal address and an opt-out line to every email (CAN-SPAM).
4. **Sorts replies** into interested, question, objection, not now, referral, wrong person, not interested, unsubscribe, out of office and bounce.
   - **Bounces, opt-outs and away messages are decided by rules, with no model request**, so they're honored even when the model is down or the daily limit is hit. "Please remove me" or "stop emailing me" is an opt-out, while a newsletter footer's "to unsubscribe..." is not.
   - Follow-up dates are worked out in code from the email's own words: "in January", "next quarter", "Q2 2027", "in 3 weeks", "out until Monday, October 19".
   - A referral's email address must be in the reply. A guessed address is replaced or flagged.
   - Drafts get the same checks as the sequences. An interested reply always gets the booking link. A question the offer doesn't answer (like price) is marked for the client. Nothing is drafted for opt-outs, bounces or away messages.
5. **Bills meetings in code:** only meetings marked held, with someone inside the meeting bar, count. A referral inherits the company size and country of the lead who referred them.

## Outputs

`write` and `replies` put their files in the same `--out` folder (default `out/outreach`):

| File | What it holds |
|---|---|
| `sequences.csv` | Sequences with nothing to fix, ready to import into the sequencer: subject plus `email_1`..`email_3` per lead (use them as custom variables in the campaign steps) |
| `sequences.xlsx` | Every lead with its status, subject, emails and checks; rows to fix in yellow |
| `review_sequences.csv` | What to fix first: held emails, then notes, then leads not written and why |
| `triage.csv` | Every reply, hottest first (interested, questions, referrals, objections), with its follow-up date and checks |
| `drafts/*.txt` | One draft answer per reply that needs one |
| `suppress.csv` | Addresses never to email again (opt-outs, clear no's, bounces). **Upload it to the sequencer's blocklist the same day** |
| `followups.csv` | When to come back to "not now" and out-of-office replies, with any other contact the reply names |
| `new_leads.csv` | People named in referrals, with the referrer's company facts |
| `invoice.csv` | From `bill`: each meeting, billable or not, and why |
| `sequences.json`, `replies.json`, `*_summary.json` | Machine-readable results, requests, tokens and cost |

Model replies are cached in `out/.../cache/`. A run stopped by the daily free limit continues where it left off.

## Try it on the example

`examples/outreach/` holds a made-up campaign for a fictional managed-IT firm, "Brightline IT":
- `campaign.toml`: the client's offer, proof points, booking link, meeting bar and prices
- `leads.csv`: 11 leads, in an Apollo export's columns plus a "Why Now" signal. Three should be held back: one with 12 staff, one in Canada, one with a broken email address.
- `replies/`: 8 replies:
  - a yes with a newsletter "unsubscribe" footer (not an opt-out)
  - a price question
  - "reach out in January"
  - "please remove me"
  - a bounce
  - an away message
  - a referral
  - "we're happy with our provider"
- `meetings.csv`: 4 meetings for the invoice; 2 are billable
- `truth.json`: the answer key that `score` compares a run against

```bash
python -m outreach leads --campaign examples/outreach/campaign.toml --leads examples/outreach/leads.csv
python -m outreach write --campaign examples/outreach/campaign.toml --leads examples/outreach/leads.csv --out out/outreach/sample
python -m outreach replies --campaign examples/outreach/campaign.toml --leads examples/outreach/leads.csv --replies examples/outreach/replies --out out/outreach/sample
python -m outreach bill --campaign examples/outreach/campaign.toml --leads examples/outreach/leads.csv --meetings examples/outreach/meetings.csv --out out/outreach/sample
python -m outreach score --run out/outreach/sample
open out/outreach/sample/sequences.xlsx
```

`write` takes 8 model requests and `replies` 5, because the bounce, the opt-out and the away message need none. With a perfect model, `score` shows all 8 sequences ready, 8 of 8 replies sorted right, both opt-outs and bounces suppressed, and both follow-up dates right. The invoice comes to $500 + 2 × $250 = $1,000.

## Using it for a client

- **Campaign file:** copy `examples/outreach/campaign.toml`. The proof list is the most important part: it's the only set of results the emails may claim, so get each line approved by the client. Put the domains you send from in `sending_domains`. Cold email usually goes out from lookalike domains, and this lets the agent find the failed address in a bounce.
- **Leads:** run leadagent first and pass its `leads.csv` (its `why_now` becomes the opening fact), or an Apollo export with a "Why Now" column you filled in. Run `leads` first to see who is held back and why.
- **Every run:** pass the last `suppress.csv` (and the sequencer's blocklist export) with `--suppress`, so nobody who opted out is written to again.
- **Replies:** export them as `.eml` files from the sending inboxes (or the sequencer's unified inbox) into one folder. Send the drafts yourself after reading them. Book interested people within hours, because replies go cold fast.
- **Billing:** keep `meetings.csv` up to date (status `held`, `no-show`, `rescheduled` or `cancelled`) and run `bill` at month end. Agree the meeting bar in writing with the client before the first campaign, since it's what the invoice is checked against.
- **Time your checking:** the model assumes about 6 hours a month per client. Track how long `review_sequences.csv` and `triage.csv` take.

## Model and privacy (read before using a client's leads)

- **Settings:** it uses the same `OPENROUTER_API_KEY` and model settings as leadagent. `OUTREACH_MODEL`, `OUTREACH_FALLBACK_MODELS`, `OUTREACH_RPM` and `OUTREACH_BASE_URL` override them.
- **Use free models only with the example.** Free-model providers may log prompts, and leads and replies are personal data. For client work, set `OUTREACH_MODEL` to a paid model and turn on zero data retention in your OpenRouter privacy settings.
- **Cost:** about 1-2k tokens per lead and per reply. That's well under $10 a month for 2,000 leads on a cheap paid model, against $500+ of revenue.
- **Compliance (not legal advice):** every email carries the client's postal address and an opt-out line. Opt-outs and bounces go to `suppress.csv`, and US law requires opt-outs to be honored within 10 business days. Follow the UK/EU "legitimate interest" rules in `analysis/PLAN.md` for contacts there.

## Not built yet

- Sending and receiving: for now `sequences.csv` is imported into Instantly or Smartlead by hand, and replies are exported as `.eml` files
- Booking straight into the client's calendar; the drafts point to the booking link
- Reply volume tracking per inbox (deliverability alerts)
- Checking whether a meeting really took place (calendar or call-recorder data); `meetings.csv` is kept by hand

## Tests

```bash
python -m pytest -q tests/test_outreach.py
```

The tests use a fake model that answers from the answer key, plus deliberate model mistakes the checks must catch. They need no network or API key.
