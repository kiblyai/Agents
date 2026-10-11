# grants: grant prospecting and first drafts for small nonprofits

Finds which funders are worth a small nonprofit's time and drafts the letter of inquiry from the nonprofit's own documents, with every fact traced to its source. It is idea #8 from the business analysis in [`analysis/`](../analysis/): a monthly retainer of about $600 for nonprofits with a $250k-$3M budget and no grant writer.

> **Status (2026-10-11):** built and tested offline on a synthetic nonprofit with a stand-in model. It has not run on a real model yet.

## What it does

**`prospect`: which funders to apply to**
1. **Reads the funder list** (CSV or Excel; any of the usual column names) and each funder's guidelines from a folder, one file per funder named after it (`great-lakes-literacy-fund.md`, `.txt`, `.pdf` or `.docx`).
2. **Rules funders out in code** before any model request:
   - invitation only ("does not accept unsolicited proposals")
   - outside the nonprofit's area (states, regions such as "Midwest", counties)
   - grants too small or too large for the nonprofit
   - deadline passed
   A deadline under two weeks away is flagged.
3. **Scores the rest** (1 request per funder): fit from 0 to 100, which program fits best, the funder's priorities, concerns and requirements, and a suggested ask.
   - Every priority, concern and requirement must quote the funder's own words. Quotes that aren't there are dropped, and a fit with no quoted priority is capped at 40.
   - The program must be one of the nonprofit's own.
   - The ask is kept inside both the funder's and the nonprofit's range.
   - A deadline the model misreads is flagged.

**`draft`: a letter of inquiry ready to edit** (1 request)
1. **Gathers the sources:** the nonprofit's profile, the funder's guidelines, the program's budget and the nonprofit's documents (annual report, program descriptions, data). A small document set goes in whole; a large one is searched per section.
2. **Drafts each section** to its word limit, citing the sources it used.
3. **Checks the draft in code.** It must be fixed when:
   - a number isn't in the sources the section cites
   - it asks for a different amount than the request, or never states the request
   - it names another funder (the classic mistake when reusing an old proposal)
   - it leaves a placeholder like `[COUNTY]` or TBD
   - a section or the whole letter runs over its limit
   - the ask is outside the funder's range, or more than the program costs
   It also notes an ask that covers over half the program's budget.

## Outputs

| File | What it holds |
|---|---|
| `prospects.csv` / `prospects.xlsx` | Funders best first: strong (70+), possible (50-69), weak, then ruled out with the reason; quoted priorities, concerns and requirements; suggested ask; deadline |
| `draft_<funder>.docx` | The letter, plus a last page with the checks and each section's sources (delete that page before sending) |
| `draft_<funder>.md` | The same letter as plain text |
| `review_draft.csv` | What to fix in the draft, must-fix first |
| `prospects.json`, `draft.json`, `prospects_summary.json` | Machine-readable results, requests and cost |

## Try it on the example

`examples/grants/` holds a made-up nonprofit, "Riverbend Youth Literacy Project" in a fictional Ohio county:
- `org.toml`: the nonprofit's profile and 3 programs
- `docs/`: 4 documents with its data
- `budget.csv`: program budgets
- `funders.csv` and `guidelines/`: 8 made-up funders. Four should be ruled out (one invitation only, one in other states, one funding only $75,000 and up, one past its deadline), one is an arts funder that should score weak, and three should score strong.
- `request.toml`: a $25,000 letter of inquiry for the summer program
- `truth.json`: the answer key

The example's deadlines assume today is 2026-10-12, so pass `--today 2026-10-12`:

```bash
python -m grants funders --org examples/grants/org.toml --funders examples/grants/funders.csv --guidelines examples/grants/guidelines --today 2026-10-12
python -m grants prospect --org examples/grants/org.toml --funders examples/grants/funders.csv --guidelines examples/grants/guidelines --today 2026-10-12 --out out/grants/sample
python -m grants draft --org examples/grants/org.toml --funders examples/grants/funders.csv --guidelines examples/grants/guidelines --today 2026-10-12 --request examples/grants/request.toml --docs examples/grants/docs --budget examples/grants/budget.csv --out out/grants/sample
python -m grants score --run out/grants/sample
open out/grants/sample/prospects.xlsx
open out/grants/sample/draft_riverbend_community_foundation.docx
```

`prospect` takes 4 model requests and `draft` 1. With a perfect model, `score` shows 8 of 8 funders sorted right, all 4 ruled out for the right reason, the best program right for all 3 strong funders, and a 5-section draft with nothing to fix.

## Using it for a nonprofit

- **Funder list:** build it from Candid's Foundation Directory, Instrumentl (if the client has it), the state's community foundations and grant portals, and the 990-PF filings of foundations that fund similar groups (ProPublica's Nonprofit Explorer is free). Save each funder's guidelines page as a file in `guidelines/`; the score is only as good as that text.
- **Documents:** the last annual report, program descriptions with real numbers, the evaluation summary, the budget and two or three past proposals. If the documents don't state a number, the draft can't use it, and that's deliberate.
- **Every month:** run `prospect` on new funders and send the client the strong ones with deadlines. Then `draft` the next letters and edit them in Word (the model assumes about 5 hours a month per client). The client signs and submits.
- **Be honest about timing:** grant decisions take 3-9 months, which is the main churn risk in the analysis. Report letters sent and invitations to apply, not only awards.

## Model and privacy (read before using a nonprofit's documents)

- **Settings:** it uses the same `OPENROUTER_API_KEY` and model settings as leadagent. `GRANTS_MODEL`, `GRANTS_FALLBACK_MODELS`, `GRANTS_RPM` and `GRANTS_BASE_URL` override them.
- **Use free models only with the example.** Free-model providers may log prompts, and budgets and internal reports are private. For a client, set `GRANTS_MODEL` to a paid model and turn on zero data retention in your OpenRouter privacy settings.
- **Cost:** about 3-5k tokens per funder scored and 10-20k per draft; a few dollars a month per client on a mid-priced paid model.
- **Check funder rules on AI:** a few funders ask applicants to disclose AI use. The client submits and is responsible for the content.

## Not built yet

- Finding funders automatically (Candid or 990-PF data); for now the funder list is built by hand
- Full proposals with attachments (budgets, logic models, board lists); only letters and narrative sections are drafted
- Tracking deadlines, submissions and reports across clients
- Reading funders' guidelines straight from their websites; save them as files for now

## Tests

```bash
python -m pytest -q tests/test_grants.py
```

The tests use a fake model that answers from the answer key, plus a reused and padded draft the checks must catch. They need no network or API key.
