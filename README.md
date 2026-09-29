# Agents

AI agents for the service businesses ranked in [`analysis/`](analysis/): the unit-economics model, launch plans and sources.

| Agent | What it does | Status | Docs |
|---|---|---|---|
| `leadagent` | Turns a list of companies into scored, cited leads with an opening line | Parked: built and tuned on real runs; next step is a real Apollo list | [leadagent/README.md](leadagent/README.md) |
| `secq` | Drafts security-questionnaire answers from a company's own documents, citing each source and flagging what needs review | Built; tested on example data | [secq/README.md](secq/README.md) |
| `medchron` | Builds a cited medical chronology from a personal-injury case's records: visits by date, treatment gaps, pre-injury records, bill totals and a review queue | Built; tested on a synthetic case; synthetic records only until the HIPAA steps are done | [medchron/README.md](medchron/README.md) |
| `freightq` | Reads a freight brokerage's quote requests and load tenders, prices each lane from the brokerage's own past loads, drafts the replies and writes tenders as TMS rows | Built; tested on a synthetic inbox | [freightq/README.md](freightq/README.md) |

All four share `agentkit/`, which holds the model client (OpenRouter by default, with rate limiting, retries, backup models and JSON repair), `.env` loading and a small PDF writer for the synthetic examples.

## Setup (once)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # then put your OPENROUTER_API_KEY in .env (git-ignored)
python -m pytest -q         # all tests run offline with fake models and websites
```

Keys live only in environment variables or the git-ignored `.env`, never in code or output files.
