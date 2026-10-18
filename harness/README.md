# harness: run every agent on its example and score it

One command runs secq, medchron and freightq on their fictional examples, compares the output with each answer key and prints one table. Paste that table back instead of three separate outputs.

## Run it

```bash
python -m harness run
```

Then paste the table it prints at the end. Other options:

```bash
python -m harness run --agents secq,freightq
python -m harness run --model google/gemma-4-31b-it:free
python -m harness run --fresh
python -m harness score
python -m harness history
```

- `--agents` runs only some agents.
- `--model` sets one model for every agent. Without it, each agent uses its own setting.
- `--fresh` deletes earlier outputs and cached model replies, so every answer comes from the model again. Use it to compare models fairly.
- `score` scores the latest outputs again without calling the model, for example after changing a scoring rule.
- `history` lists earlier runs with their commit, model, score and cost.

A full run takes about 22 model requests (secq 2, medchron about 9, freightq 11), so it fits in the free 50 a day. If the daily limit stops it, run the same command again the next day. Finished work comes from the cache.

## What the result means

| Result | Meaning |
|---|---|
| PASS | Run complete, no errors, and at least 90% of checks right |
| FAIL | An error, or under 90% of checks right |
| INCOMPLETE | Stopped early (daily limit) or some batches failed; run again |
| CRASHED | The agent exited with an error; see `run.log` in its output folder |

**Errors** are outputs that must never reach a client, whatever the score:
- **secq:** a Yes to a question the documents don't cover, or a wrong answer that isn't flagged for review
- **medchron:** diagnosis codes or bill lines that aren't in the records, or a wrong total billed
- **freightq:** a wrong price, or a lane that isn't in the emails

## Answer keys

- `examples/secq/truth.json`: written by hand from the Northwind documents. Update it if you change the sample questionnaire or the documents.
- `examples/medchron/truth.json` and `examples/freightq/truth.json`: generated with the examples (`python -m medchron sample`, `python -m freightq sample`).

leadagent and leadactor aren't included: they need live websites and have no answer key.

Outputs go to `out/harness/<model>/<agent>/`, with each agent's usual files plus `run.log`. The history is `out/harness/history.jsonl`. Both are git-ignored.
