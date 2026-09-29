# leadagent

A research agent that turns a list of companies into scored, cited leads with a ready-to-send opening line. It is idea #1 from the business analysis in [`analysis/`](analysis/).

## What it does

For each company in your CSV:

1. **Reads its website.** It fetches the homepage plus up to 3 pages that carry buying signals (careers or job board, news or blog, about, customers), and honours `robots.txt`.
2. **Judges each target criterion** (industry, size, funding stage, geography, exclusions) as met, not met or unknown, citing evidence. It scores fit 0-10 and lists the reasons to reach out now, each citing the page it came from.
3. **Applies the checks a model can't be trusted with:**
   - drops signals that cite pages it never read
   - marks a signal *verified* only if its evidence is actually on the cited page
   - flags anything dated more than 12 months ago as old news, not a reason to reach out now
   - caps the score at 4 when any criterion is clearly not met, or when the company is excluded, acquired or shut down
   - requires numbers and funding rounds in the evidence to match the source exactly
   - builds the "why now" only from a verified, current signal
4. **Writes one opening line** that speaks to the reader ("you"/"your") about one verified fact. It rejects placeholders, lines that are too short or too long, banned phrases and `!`, and retries once.
5. **Matches contacts** from your CSV (for example an Apollo export) to your target titles.

It writes to `out/`:

| File | What it holds |
|---|---|
| `leads.csv` | One row per contact at each qualifying company: score, why-now, signals, sources, contact, email status, opening line |
| `companies.csv` | Every company with its status (`qualified`, `not_fit`, `unreachable`, `error`) and reasons |
| `qa_sample.csv` | A random 5% of lead rows (at least 5) to check by hand before delivery |
| `companies.jsonl` | Full research records |
| `run_summary.json` | Counts, model requests, tokens, cost |

Researched companies are cached in `out/cache/`. Re-running skips them, so an interrupted run, or one that hit the daily free limit, continues where it stopped. Unreachable sites and errors are retried on the next run. Editing the spec, or updating to a version with changed research logic, invalidates the cache.

## Setup

```bash
pip install -e ".[dev]"
python -m leadagent check               # one tiny request to confirm key and model
```

Pick a model. The default `openrouter/free` sends each request to whichever free model is available, which gives uneven results. List the free models and pin one that supports JSON output:

```bash
python -m leadagent models --free          # "json yes" = the model supports JSON output mode
echo 'LEADAGENT_MODEL=<model id>' >> .env
```

`run_summary.json` shows which models actually served your requests (`models_used`), and error messages name the model that failed.

Free models are shared, so one can be busy ("rate-limited upstream") for a while. Give it backups, which OpenRouter tries in order (up to 3):

```bash
echo 'LEADAGENT_FALLBACK_MODELS=nvidia/nemotron-3-super-120b-a12b:free' >> .env
```

If a model stays busy for 3 companies in a row, the run stops instead of burning your daily requests. Re-run later; finished companies are skipped.

API keys live only in environment variables, never in code, specs or output files. Set `OPENROUTER_API_KEY` (from openrouter.ai/keys) in one of these places:

- **Your shell:** `export OPENROUTER_API_KEY=...`
- **A local `.env` file:** copy `.env.example` to `.env`. `.env` is git-ignored and loaded at startup.
- **Your hosting or cloud-environment settings,** as an environment variable.

A variable already set in the environment always wins over `.env`. The key is never printed or saved in `out/`.

## Run

```bash
cp examples/icp.example.toml my_client.toml     # edit for the client
python -m leadagent run --icp my_client.toml --companies companies.csv --out out/client_a --limit 25
```

- In the spec, `stages` sets the funding stages you want, and `[writer] offer` describes what you sell, so the opening line stays relevant without pitching.
- `--companies` needs a column called `domain` or `website`. Optional contact columns: `First Name`, `Last Name`, `Title`, `Email`. Contacts can also come from a separate file via `--contacts`.
- **Add company data columns if you have them.** Apollo-style exports include `# Employees`, `Latest Funding`, `Latest Funding Amount`, `Last Raised At`, `Total Funding`, `Industry` and `Company Country`. They are passed to the model as trusted data and can be cited as sources.
  - Websites rarely state headcount or funding stage. So when the spec asks for a size or stage and neither can be found, the company is kept below `min_score` and flagged for a manual check. Set `require_size_or_stage = false` to turn this off.
- `--model <id>` picks a model (default `openrouter/free`). `--web-search` adds OpenRouter web search for recent news. `--rpm` and `--concurrency` control speed.

## Limits and costs

- **Requests.** About 2 model requests per company (research plus opening line), plus 1 more when a reply needs fixing.
- **Free models** (`openrouter/free` or any `:free` id). OpenRouter caps them at 20 requests/minute and 50 requests/day, rising to 1,000/day once you have bought $10 of credit. That is about 25 companies a day without credit and about 500 with it.
- **Paid models.** OpenRouter shows cost per request, and `run_summary.json` totals it. OpenRouter adds a ~5.5% fee when you buy credit. Web search costs about $4 per 1,000 results, so 3 results per company is about $0.012.
- **Privacy.** Only public website text and your spec are sent to the model. Contact names and emails are never sent. Some free-model providers may log or train on prompts, which you can switch off in your OpenRouter privacy settings.
- **Client work.** Use a paid model for client deliveries. Free models change without notice and are rate-limited.
- **JavaScript-only websites** return little readable text, so they tend to score low. Check them in `companies.csv`.

## Not built yet

- **Company discovery.** You supply the company list, for example an Apollo export.
- **Email finding and verification.** Emails are only syntax-checked (`unverified`) until you choose a provider. `leadagent/contacts.py` has an `EmailVerifier` interface for plugging one in.
- **CRM or sequencer push.** Import `leads.csv` instead.

## Tests

```bash
python -m pytest -q
```

The tests use a fake model and fake websites, so they need no network or API key.
