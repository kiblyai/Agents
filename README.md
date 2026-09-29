# leadagent

A research agent that turns a list of companies into scored, cited leads with a ready-to-send opening line. It is idea #1 from the business analysis in [`analysis/`](analysis/).

## What it does

For each company in your CSV:

1. **Reads its website.** It fetches the homepage plus up to 3 pages that carry buying signals (careers or job board, news or blog, about, customers), and honours `robots.txt`.
2. **Scores fit against your target-customer spec** (0-10) and lists the reasons to reach out now. Each reason cites the page it came from.
3. **Checks the citations.** It drops any signal whose cited page it never read, and marks a signal *verified* only if its evidence is actually on that page. This catches invented facts.
4. **Writes one opening line** from the strongest verified signal. It rejects lines that are too long, use banned phrases or contain `!`, and retries once.
5. **Matches contacts** from your CSV (for example an Apollo export) to your target titles.

It writes to `out/`:

| File | What it holds |
|---|---|
| `leads.csv` | One row per contact at each qualifying company: score, why-now, signals, sources, contact, email status, opening line |
| `companies.csv` | Every company with its status (`qualified`, `not_fit`, `unreachable`, `error`) and reasons |
| `qa_sample.csv` | A random 5% of lead rows (at least 5) to check by hand before delivery |
| `companies.jsonl` | Full research records |
| `run_summary.json` | Counts, model requests, tokens, cost |

Researched companies are cached in `out/cache/`. Re-running skips them, so an interrupted run, or one that hit the daily free limit, continues where it stopped. Unreachable sites and errors are retried on the next run. Editing the spec invalidates the cache.

## Setup

```bash
pip install -e ".[dev]"
export OPENROUTER_API_KEY=sk-or-...     # from openrouter.ai/keys
python -m leadagent check               # one tiny request to confirm key and model
```

## Run

```bash
cp examples/icp.example.toml my_client.toml     # edit for the client
python -m leadagent run --icp my_client.toml --companies companies.csv --out out/client_a --limit 25
```

- `--companies` needs a column called `domain` or `website`. Optional contact columns: `First Name`, `Last Name`, `Title`, `Email`. Contacts can also come from a separate file via `--contacts`.
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
