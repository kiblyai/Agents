# txcoord: real-estate contract deadlines, calendar and checklist

Reads a home purchase contract and its addenda and produces what a transaction coordinator builds by hand:
- every deadline as a date
- a calendar file with reminders
- a checklist
- the intro email to all parties, and dated reminder emails

The model only copies the contract's wording; code works out every date. It is idea #9 from the business analysis in [`analysis/`](../analysis/): coordination for solo agents and small teams at about $250 per file.

> **Status (2026-10-11):** built and tested offline on synthetic contracts with a stand-in model. It has not run on a real model or a real contract yet. **Every timeline needs a person's check against the signed contract before it goes out.**

## What it does

1. **Reads each transaction folder** (one subfolder per file): the contract and its addenda as PDF, Word or text. The file named `contract...`, `purchase...` or `agreement...` is read first, then addenda in name order.
2. **Reads the terms with the model** (1 request per file): parties, property, price (in figures and in words), earnest money, financing, the acceptance and closing dates, every deadline's wording exactly as written ("within ten (10) days after the Acceptance Date"), the changes each addendum makes, and the contacts.
3. **Works out every date in code**, never by the model:
   - Calendar or business days from acceptance or closing, or a date written out. "24 hours before closing" is the day before.
   - Business days skip weekends and US federal holidays (with Saturday and Sunday holidays moved to the observed weekday).
   - The contract's own rules win: its definition of "days", and whether a deadline on a weekend or holiday moves to the next business day. When it says nothing, `tc.toml` decides and the file is flagged.
   - A deadline that depends on an event ("7 days after receipt of the title commitment") is listed without a date, to set when it happens.
4. **Checks the contract** (these rules are code):
   - The acceptance date, closing date, price, earnest money and each deadline's wording must be in the documents.
   - An addendum that moves closing wins, even if the model missed it.
   - The price in words must match the figure: "Three Hundred Eighty-Nine Thousand Five Hundred" isn't $389,000.
   - A contingency must not end after closing or before acceptance.
   - Unusual earnest money (over 10% or under 0.5%), a loan plus down payment that doesn't equal the price, and closing on a weekend or holiday are all flagged.
   - Contact emails must appear in the documents.

## Outputs

In `--out` (default `out/txcoord`), one folder per transaction plus the overview:

| File | What it holds |
|---|---|
| `<file>/timeline.csv` | Every deadline: date, weekday, status (past, soon, upcoming, no date), who acts, how the date was worked out, the contract's words |
| `<file>/calendar.ics` | All-day events with reminders 3 and 1 days before; import into Google Calendar or Outlook |
| `<file>/checklist.md` | One task per deadline, with what to fix first |
| `<file>/emails/intro.txt` | The key-dates email to every party |
| `<file>/emails/reminders/*.txt` | One reminder per deadline, named by the day to send it, to the people who must act |
| `all_deadlines.csv` | Every deadline across all files, by date: the coordinator's week at a glance |
| `review.csv` | What to fix before anything goes out, then things to know |
| `results.json`, `run_summary.json` | Machine-readable results, requests, tokens and cost |

## Try it on the example

`examples/txcoord/` holds three made-up transactions in a fictional town, written on a sample form (not any state's or association's real form):
- **`101_maple_ave`:** a financed purchase with 8 deadlines and an addendum that moves closing. The 10-day inspection period ends on Columbus Day, so it moves to the next day, the 30-day loan deadline falls on a Sunday, and title objections depend on an event.
- **`22_birch_ct`:** a clean cash purchase, with two deadlines already past.
- **`9_cedar_ln`:** the price in words doesn't match the figure, and the inspection period ends after closing. Both must be fixed. The earnest money is unusually high.

The example's dates assume today is 2026-10-12:

```bash
python -m txcoord files --files examples/txcoord/transactions
python -m txcoord run --files examples/txcoord/transactions --tc examples/txcoord/tc.toml --today 2026-10-12 --out out/txcoord/sample
python -m txcoord score --run out/txcoord/sample
open out/txcoord/sample/101_maple_ave/checklist.md
```

The run takes 3 model requests. With a perfect model, `score` shows all acceptance and closing dates right, 17 of 17 deadlines right, 2 files ready and 1 held, and 4 of 4 expected checks with no other must-fix checks.

## Using it for an agent

- **Per file:** make a folder named after the property and save the fully signed contract and every addendum or counteroffer in it. Scanned contracts need text recognition first (e.g. `ocrmypdf`, as in medchron).
- **Settings:** copy `examples/txcoord/tc.toml`, then set your name and email, and what "days" means when the state's form doesn't say. Add local holidays to `extra_holidays` (e.g. the day after Thanksgiving).
- **Every morning:** re-run with today's date. Statuses update, `all_deadlines.csv` shows the week, and the reminders named with today's date are the ones to send.
- **Time your checking:** the kill rule in `analysis/model.py` is more than 1.5 hours of your time per file after automation. Time the review of `timeline.csv` against the contract.
- **Your responsibility:** a missed deadline can cost a client their earnest money. Treat this as a second pair of eyes that does the counting, not as the authority. Confirm with the agents in writing.

## Model and privacy (read before using a real contract)

- **Settings:** it uses the same `OPENROUTER_API_KEY` and model settings as leadagent. `TXCOORD_MODEL`, `TXCOORD_FALLBACK_MODELS`, `TXCOORD_RPM` and `TXCOORD_BASE_URL` override them.
- **Use free models only with the example.** Contracts carry names, addresses, prices and loan details, and free-model providers may log prompts. For real files, set `TXCOORD_MODEL` to a paid model and turn on zero data retention in your OpenRouter privacy settings.
- **Cost:** a 15-page contract is about 10-15k tokens, a few cents per file on a mid-priced paid model.

## Not built yet

- State-specific forms and rules (each state's association form numbers its sections differently; the checks read wording, not section numbers)
- Sending the reminders, and syncing with transaction tools (SkySlope, Dotloop) or a shared calendar
- Tracking which deadlines were met, and waivers or extensions signed later (add them as addenda and re-run)
- Commission and closing-cost checks

## Tests

```bash
python -m pytest -q tests/test_txcoord.py
```

The tests use a fake model that answers from the answer key, plus deliberate model mistakes the checks must catch. They need no network or API key.
