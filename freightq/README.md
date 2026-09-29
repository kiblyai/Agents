# freightq: freight-broker quote agent

Reads the quote requests and load tenders that shippers email to a freight brokerage. For each lane it prices the load from the brokerage's own past loads, drafts the reply, and lists what a person must check. It is idea #6 from the business analysis in [`analysis/`](../analysis/), a service for US brokerages with 3-25 staff at about $2,500 setup plus $1,000 a month.

> **Status (2026-09-29):** built and tested offline on a synthetic inbox with a stand-in model. It has not run on a real model yet.

## What it does

1. **Reads the inbox:** a folder of emails, in file-name order.
   - `.eml` files (drag emails out of Apple Mail or Outlook to a folder) and `.txt` files (a pasted email with `From:`, `Subject:` and `Date:` lines at the top).
   - PDF and text attachments are read too, so a load tender sent as a PDF works.
   - Quoted history in a reply is kept as context, and the newest message wins.
2. **Reads each email with the model** (1 request per email):
   - what kind it is: a quote request, a tender, or something else (such as a carrier offering a truck)
   - every lane: origin, stops, destination, dates and times, equipment, weight, commodity, pieces, temperature, hazmat, special needs, reference numbers, a rate the sender gave, and the number of trucks
   - "tomorrow" or "Thursday" is worked out from the day the email was sent
3. **Checks every value against the email** (these rules are code, not model judgment):
   - It removes a ZIP code, weight, date, rate, reference number or special need that isn't in the email, and asks for it instead.
   - The equipment must be stated. A "van" the email never mentions is removed, and the reply asks.
   - It flags a ZIP code in the wrong state, and a lane whose origin and destination look swapped.
   - Hazmat is decided by the email's words ("UN1263", "Class 3", "placards"), not by the model, and "no hazmat" doesn't count.
   - It flags weights over the usual limit for the equipment, and LTL-size shipments (10,000 lbs or less, or under 6 pallets).
   - It lists what's missing: by default origin, destination, equipment, pickup date and weight, plus the temperature for a reefer.
4. **Prices each lane in code** from the lane history (the model never sets a price):
   - It finds past loads with the same equipment on the same city pair, then the same 3-digit ZIP areas. It uses the 5 most recent from the last 120 days.
   - Carrier pay is the median of those loads. The customer rate adds the margin (15%, at least $150) and is rounded up to $25.
   - Extra charges are added for stops, tarps, hazmat, team drivers and liftgate.
   - When only state-to-state history exists, or none at all, the lane is left for pricing by hand.
   - For a tender, it checks the tendered rate against recent carrier pay.
   - It flags thin history, a wide spread of past carrier pay, stale history, and a big change from what this customer paid last time.
5. **Writes the drafts and the review list.** Replies are filled in by code from the checked values. A lane it can't price says `[[PRICE BY HAND]]`, so it can't go out unnoticed.

Outputs in `--out` (default `out/freightq`):

| File | What it holds |
|---|---|
| `replies/*.txt` | One draft reply per email, ready to paste into the mail client |
| `review.csv` | What to check, most urgent first: tenders to accept or decline, then lanes to price or check, then emails that weren't requests |
| `loads.csv` | Tenders as rows for a TMS import (customer, load number, stops, dates, equipment, weight, rate, estimated carrier pay) |
| `quotes.xlsx` | Every lane with its price and checks (rows to check in yellow), the past loads each price came from, every email, and the review list |
| `results.json` | Machine-readable, used by `score` |
| `run_summary.json` | Counts, lane-history rows skipped, requests, tokens, cost |

Model replies are cached in `out/.../cache/`. A run stopped by the daily free limit continues where it left off.

## Try it on the example

`examples/freightq/` holds a made-up inbox for a fictional brokerage, "Coho Freight Brokerage": 11 emails with 12 lanes. It covers:

- a plain van quote, a reefer quote with no temperature, and a flatbed that needs tarps
- one email asking for 3 lanes: one priced from the same cities, one from nearby ZIP codes, and one with only state-level history
- a load tender as a PDF attachment, with a stop-off and PO numbers
- an overweight van load, and a hazmat load
- a reply thread that changes the consignee and the number of trucks
- a carrier offering a truck, which is not a request
- an LTL-size shipment, and a vague request with no destination

`lanes.csv` is its lane history: 41 past loads, plus one row with equipment the pricing doesn't know, to show how unusable rows are skipped. `broker.toml` holds its settings, and `truth.json` is the answer key that `score` compares a run against.

```bash
python -m freightq inbox --emails examples/freightq/inbox
python -m freightq run --emails examples/freightq/inbox --lanes examples/freightq/lanes.csv --broker examples/freightq/broker.toml --out out/freightq/sample
python -m freightq score --run out/freightq/sample
open out/freightq/sample/review.csv
open out/freightq/sample/replies
```

The run takes 11 model requests. With a perfect model, `score` shows 12 of 12 lanes, 9 of 9 rates and 6 of 6 expected flags, with no other flags.

To price one lane without the model (handy for checking the lane history):

```bash
python -m freightq price --from "Dallas, TX 75207" --to "Atlanta, GA" --equipment van --lanes examples/freightq/lanes.csv --broker examples/freightq/broker.toml --date 2026-09-21
```

## Using it for a brokerage

- **What to ask the prospect for:** 20-50 recent quote-request and tender emails (`.eml`), and a 90-day load export from their TMS with carrier pay. The kill rule in `analysis/model.py` fires if no prospect will share sample quote emails, so this request is also the test.
- **Lane history (`--lanes`):** a CSV with one row per past load. It needs origin, destination, equipment and carrier pay. Date, customer, miles and customer rate are optional but help. Common TMS column names are recognized, such as `Pickup City`, `Delivery Zip`, `Trailer Type`, `Carrier Rate` and `Revenue`. Rows it can't use are listed in `run_summary.json`.
- **Settings (`--broker`):** copy `examples/freightq/broker.toml` and change the name, signature, margin, extra charges, weight limits and required fields.
- **Show the result:** send the prospect 5 drafted replies next to what they actually quoted.
- **Time your checking:** time how long `review.csv` and the drafts take per email. The model assumes about 4 hours a month per brokerage.

## Model and privacy (read before using a brokerage's emails)

- **Settings:** it uses the same `OPENROUTER_API_KEY` and model settings as leadagent. `FREIGHTQ_MODEL`, `FREIGHTQ_FALLBACK_MODELS`, `FREIGHTQ_RPM` and `FREIGHTQ_BASE_URL` override them.
- **Never use free models with a brokerage's real emails or rates.** Free-model providers may log prompts, so they're for the example only. The tool prints a warning when a free model is selected.
- **For client work:** set `FREIGHTQ_MODEL` to a paid model and turn on zero data retention in your OpenRouter privacy settings.
- **Cost:** each email is one request of about 1-3k tokens, roughly $0.01-0.02 on a mid-priced paid model. That's $30-60 a month at 3,000 emails, against $1,000 of revenue.
- **Every draft needs a person's check before it goes out,** and every tender needs a person's decision.

## Not built yet

- Watching a shared inbox (Gmail or Outlook) and saving the replies as drafts there; for now, emails go through a folder
- A direct TMS connection; `loads.csv` needs mapping to each TMS's import format
- Market rates (DAT, Greenscreens) for lanes with no history
- Miles and rate-per-mile pricing; for now, prices come only from past loads on the same or nearby lanes
- Excel lane bids (RFQs with dozens of lanes); an email with a long lane list may need a bigger `max_tokens`
- Outlook `.msg` files; save them as `.eml` first
- LTL pricing; LTL-size shipments are flagged for pricing by hand

## Tests

```bash
python -m pytest -q
```

The tests use a fake model that answers from the answer key, plus deliberate model mistakes the checks must catch. They need no network or API key.
