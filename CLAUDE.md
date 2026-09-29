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
| `agentkit/` | Shared: OpenRouter model client (rate limit, retries, backup models, JSON repair, daily-limit stop) and `.env` loading | none |
| `leadagent/` | **Parked.** Company list in, scored and cited leads with opening lines out. Tuned over 3 real runs. | Test on about 20 real Apollo prospects; see `leadagent/README.md` |
| `secq/` | **Active.** Security-questionnaire drafting with citations and review flags. Tested on the fictional example with a stand-in model only. | Founder runs the example on their Mac and sends `review.csv` plus the draft xlsx; check answer quality, then fix |
| `medchron/` | **Active.** Medical chronology for personal-injury firms: PDFs in, cited chronology (Word), gaps, bills, review queue and a bookmarked combined PDF out. Tested offline on the synthetic case with a stand-in model only. | Founder runs the synthetic case on their Mac (`--synthetic`, free model) and sends the `score` output plus `review.csv`; check extraction quality, then fix |
| `examples/` | Sample inputs: a fictional company ("Northwind Analytics") for secq, and a fictional patient's case file with an answer key (`examples/medchron/truth.json`) for medchron. Regenerate the medchron case with `python -m medchron sample`, never by hand (a test compares them). | none |
| `tests/` | Run offline with fakes (`tests/fakes.py`): `python -m pytest -q` | none |

## Rules we agreed
- **Keys:** `OPENROUTER_API_KEY` lives in environment variables or the git-ignored `.env`. Never write it to code, specs, output or chat. The founder once pasted a key into `.env.example` on GitHub; secret scanning blocked the commit. Watch for this.
- **Models (via OpenRouter):** main `nvidia/nemotron-3-super-120b-a12b:free`, backup `google/gemma-4-31b-it:free`. Free models are shared and often busy, capped at 20 requests/min and 50/day (1,000/day after $10 of credit). Both agents stop cleanly at the daily limit and resume from cache.
- **Privacy:** free models may log prompts. Use them only with public or example data, never a client's real documents. For client work, use a paid model with zero data retention.
- **Medical records (medchron):** real records are HIPAA-protected. Only synthetic records until the founder has a BAA with the model provider (`analysis/PLAN.md` 3.3 step 1). The code enforces it: a run needs `--synthetic`, or a paid model plus `MEDCHRON_BAA=yes`. Never weaken this check.
- **Checks in code:** the model drafts, and code checks citations, numbers, dates and hard criteria. When a real run shows a bad output, add a regression test named after the real case (e.g. "the Supabase case") and fix the code.
- **Git:** develop on `claude/determined-brown-576j98`. Bump `CACHE_VERSION` in an agent's `pipeline.py` when its research or drafting logic changes.

## Founder's machine (they run everything locally and paste results back)
- Mac mini, zsh, repo at `~/Agents`, venv at `.venv`, Python 3.12 from Homebrew.
- zsh doesn't accept `#` comments in pasted commands, so give commands without inline comments.
- After pulling, `pip install -e ".[dev]"` may be needed when dependencies change.

## Cloud-session gotchas
- This container can't reach openrouter.ai or ordinary websites (network policy). Test with fakes or a local stand-in server on 127.0.0.1.
- `pkill -f <pattern>` kills your own shell if the pattern appears in the same command line. Use a pattern that doesn't.
- If `pypdf` fails to import (broken system `cryptography`): `pip install --ignore-installed cffi cryptography`.
