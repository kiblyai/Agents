---
name: eval
description: Run the eval harness (secq, medchron, freightq on their fictional examples, scored against answer keys), explain the results in plain language, find the root cause of every FAIL or error and fix it. Use when the user types /eval, asks to "run the evals", "test the agents", "score the agents" or "compare models".
argument-hint: "[--agents secq,medchron,freightq] [--model <openrouter id>] [--fresh] [--fix]"
allowed-tools: Bash(python -m harness *), Bash(.venv/bin/python -m harness *), Bash(python -m pytest *), Bash(.venv/bin/python -m pytest *), Bash(git diff *), Bash(git status *), Read, Grep, Glob
---

# /eval: run the harness, explain it, fix what failed

Arguments: `$ARGUMENTS` (pass them on to `harness run`, except `--fix`).

## 1. Run

- Use `.venv/bin/python` if `.venv/` exists, otherwise `python`.
- Run `python -m harness run $ARGUMENTS` (without `--fix`) and let it finish. A full run is about 22 model requests and a few minutes on free models.
- If every agent crashes with connection errors to openrouter.ai, you are in a cloud session without network access. Say so and offer `python -m harness score` on existing outputs instead. Don't try to work around the network policy.
- If `OPENROUTER_API_KEY` is missing, tell the user to put it in `.env`. Never print, echo or write the key anywhere.

## 2. Report (short, plain language)

Start with the table the harness printed, unchanged. Then give one line per agent: what it got right, what went wrong, and whether that matters for a client. Results:
- **INCOMPLETE** because of the daily limit: say so and tell the user to run `/eval` again after the limit resets. Finished work comes from the cache. Stop here for that agent.
- **CRASHED:** read `out/harness/<model>/<agent>/run.log` and give the actual error.
- **PASS:** say so in one line and move on.

## 3. Diagnose every FAIL and every error

Errors (lines starting `ERROR`) come first: they are outputs that must never reach a client. For each one:
1. Open the agent's output in `out/harness/<model>/<agent>/` (secq `review.csv`, medchron `chronology.json`, freightq `results.json`) and the answer key (`examples/<agent>/truth.json`). Find the exact item.
2. Decide which of these it is:
   - **Model mistake that code should have caught:** a number, date, code or citation that isn't in the source. This is the usual case. The fix is a code check, never a prompt tweak alone.
   - **Code bug:** parsing, pricing, merging or scoring logic.
   - **Answer key is wrong:** only when the source documents clearly support the agent's output. Show the user the source text and ask before changing a `truth.json`. Never edit an answer key just to make a score pass. Regenerate the medchron and freightq examples with `python -m medchron sample` / `python -m freightq sample`, never by hand.
   - **Model just weak:** misses with no wrong output (lower recall). Note it. Suggest trying the backup model (`--model google/gemma-4-31b-it:free`) rather than changing code.
3. Give the evidence: the item, what the agent output, what the source says, and the file and line of the code involved.

## 4. Fix (only with `--fix` in the arguments, or after the user says yes)

Follow the repo rules in `CLAUDE.md`:
- Add a regression test in `tests/test_<agent>.py` named after the case (e.g. `test_the_hazmat_tender_case`). It reproduces the failure offline with the fakes in `tests/fakes.py`, and it fails before the fix.
- Make the smallest code change that fixes it. Prefer a code check (citations, numbers, dates, hard criteria) over prompt wording.
- Bump `CACHE_VERSION` in the agent's `pipeline.py` if the research or drafting logic changed.
- Run `python -m pytest -q`; all tests must pass.
- Re-check with `python -m harness score --agents <agent>` (no model calls). Use `python -m harness run --agents <agent>` only if the cache version changed and the user agrees to spend the requests.
- Never weaken the medchron privacy check (`--synthetic` or paid model plus `MEDCHRON_BAA=yes`). The harness only uses fictional example data. Never point it at a client's real documents.

Don't commit or push unless the user asks.

## 5. End with next steps

One to three concrete next steps, for example "run `/eval --agents freightq` to confirm the fix" or "compare with the backup model: `/eval --model google/gemma-4-31b-it:free --fresh`". Point to `python -m harness history` when comparing with earlier runs.
