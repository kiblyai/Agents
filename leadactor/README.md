# leadactor: the lead agent as an Apify Store Actor

Idea #4 from the business analysis in [`analysis/`](../analysis/): sell an AI agent on the Apify Store, priced per use. Apify's Store search brings the users, so there is no cold email to send.

This Actor is `leadagent` packaged for the Store as **AI Lead Scorer**. Apify users give it company websites (or another Actor's results, such as Google Maps Scraper output) and describe who they sell to. For each company they get a 0-10 fit score, buying signals quoted from the page they came from, and an opening line when the company fits. They pay per company scored.

> **Status (2026-09-30):** built and tested offline with a stand-in model, including the Apify SDK's billing run locally. It has not run on a real model or on Apify yet. Next: steps 1-3 below.

## What it adds to leadagent

The research, the checks and the opening lines are leadagent's, unchanged (see [leadagent/README.md](../leadagent/README.md)). These rules are new, and they are code, not settings:

- **One row, one charge.** A company is charged only when its website was read and scored. Unreachable sites, model errors and unusable input rows (a LinkedIn or Google Maps link instead of a website, a typo) are free. They are listed in the run's `OUTPUT` summary, not the dataset.
- **No unpaid work.** Before starting each company it checks that the user's maximum charge per run can still pay for it, counting companies already in progress.
- **No free models for paying users.** A pay-per-event run with a free model (or a free backup model) stops before doing anything. Free models may log prompts and are capped at 50-1,000 requests a day.
- **No double charges.** If Apify restarts a run, companies already in its dataset are skipped.
- **Setup checks at start:** the key is set, and the pricing includes a priced `company-scored` event.
- **Users never see model names or costs.** Your costs appear only in local runs.

## Step 1: try it on your Mac with the free model (10 minutes)

```bash
pip install -e ".[dev]"
python -m leadactor run --input examples/leadactor/input.example.json --out out/leadactor --price 0.03 --max-charge 0.50
open out/leadactor/results.csv
```

The example scores 6 real websites for about 10-15 model requests, which fits the free limit of 50 a day. Two rows are free on purpose: a made-up domain (unreachable) and a LinkedIn link (not a company website). `run_summary.json` lists them under `notScoredList`. `dataset.jsonl` is exactly what an Apify user gets.

`--price` and `--max-charge` simulate what a user would pay. Try `--max-charge 0.07`: it stops after 2 scored companies.

## Step 2: measure cost and quality with a paid model (about $0.10)

On Apify the Actor must use a paid model. Pick a cheap one that supports JSON output:

```bash
python -m leadagent models
python -m leadactor run --input examples/leadactor/input.example.json --out out/leadactor-paid --model <paid model id> --price 0.03
```

The last line reads like `Model cost per scored company: $0.0040, 13% of the $0.03 price (keep it under 25%: fine)`. The business model assumes model costs stay under about 25% of what users pay. If yours is higher, pick a cheaper model or raise the price.

Then check quality. Open `results.csv`, compare 5 rows with the websites, and send me any bad row. I'll add a regression test for it, as with leadagent.

## Step 3: publish on Apify (about 30 minutes)

1. Create a free account at apify.com. Install the Apify command-line tool and log in:

   ```bash
   brew install apify-cli
   apify login
   ```

2. Store the key and model as Apify secrets. The key goes from `.env` straight to Apify without being printed or saved in a file:

   ```bash
   apify secrets add openrouterApiKey "$(grep '^OPENROUTER_API_KEY=' .env | cut -d= -f2-)"
   apify secrets add leadactorModel <paid model id>
   ```

3. Upload and build, from `~/Agents`:

   ```bash
   apify push
   ```

   This uploads only what the Actor needs (`.actor/`, `agentkit/`, `leadagent/`, `leadactor/`). Git-ignored files such as `.env` and `.venv/` are never uploaded (`.actorignore` lists the rest).

4. **Test run.** In Apify Console, open the Actor (`ai-lead-scorer`), click Start with the prefilled input, and check the dataset and the `OUTPUT` record. Before you set a price, your runs cost only Apify's platform usage.

5. **Set the price.** In the Actor's Publication tab, under Monetization, choose pay per event and add one event:
   - name: `company-scored` (it must match exactly)
   - title: Company scored
   - price: **$0.03** (see below)

   Don't add a price per dataset item. Every row is already one `company-scored` charge.

6. **Publish** from the same tab. The title and description come from `.actor/actor.json`, and the Store page is `leadactor/STORE.md`. Pick the categories Lead generation and AI.

After a code change, `apify push` again. Apify's monetization guide: https://docs.apify.com/platform/actors/publishing/monetize

## Why $0.03 a company

- The model in `analysis/model.py` assumes $10 per user per month, about 330 companies at $0.03. It also assumes model costs of about 25% of revenue and Apify usage of about 8%.
- You keep 80% of what users pay, after the platform usage of their runs is deducted (`analysis/SOURCES.md`).
- Before publishing, look at what similar Actors charge (search the Store for "lead scoring" and "company enrichment"). Apify limits how often you can change a price, so pick one you can keep.

## 30-day test and kill rule

- **Kill** if fewer than 20 different users run it in the first 30 days after it is listed (`analysis/model.py`). The Actor's analytics in Apify Console show the user count.
- **Each month,** compare your OpenRouter spend (openrouter.ai/activity) with the Actor's earnings in Apify Console. Model spend should stay under 25% of earnings.

Be clear-eyed: in the base case this idea earns about $11/h by month 12, far below your $101/h line (`analysis/results.md`). It is worth a listing only because it reuses leadagent and costs about an hour to publish. Its other value is as a free test of whether anyone wants leadagent's output.

## Limits and what's not built

- **No proxy.** Sites that block cloud servers come back unreachable (free for the user, but no revenue for you). If more than about 1 in 5 real sites are unreachable on Apify, add Apify Proxy.
- **No web search, and no finding people or emails.** The Store page points users to a contact-finding Actor for companies that qualify.
- **Speed.** 6 companies at a time and 60 model requests a minute, set in `.actor/actor.json`. Raise both if paid runs are slow.

## Files

| File | What it is |
|---|---|
| `leadactor/actor.py` | Input checks, charging rules, the run loop and the dataset row |
| `leadactor/platforms.py` | Apify (through the Apify SDK) and local runs |
| `leadactor/STORE.md` | The Store page users see |
| `leadactor/requirements.txt` | What the Apify image installs |
| `.actor/` | Apify's files: `actor.json` (name, secrets, memory), input form, dataset views, output links, `Dockerfile` |
| `examples/leadactor/input.example.json` | The example input for step 1 |

## Tests

```bash
python -m pytest -q
```

They run offline with a fake model and fake websites. If the `apify` package is installed (`pip install -e ".[actor]"`), one more test runs the real Apify SDK's billing locally.
