# secq: security-questionnaire agent

Drafts answers to a security questionnaire from a company's own documents, cites the source of every answer, and flags anything a person must check. It is idea #2 from the business analysis in [`analysis/`](../analysis/), a service charging about $500 per questionnaire.

## What it does

1. **Reads the knowledge base:** a folder of the company's documents.
   - PDF (for example the SOC 2 report), Word (`.docx`), Markdown and text are split into short, citable passages such as `policy.pdf p.3` or `access.md § MFA`.
   - Past answered questionnaires (`.xlsx`/`.csv`) become a library of approved answers, and their wording is reused.
2. **Reads the questionnaire.** It finds the question, answer, comment and ID columns and the section titles on every sheet, and skips questions that are already answered. Check with `secq inspect`.
3. **Finds the sources for each question** with keyword search tuned for security wording, so "2FA", "multi-factor" and "MFA" all match.
4. **Drafts answers in batches** (8 questions per model request) in the company's voice. Each answer has a short response (Yes/No/Partial/N/A or a fact), an explanation, source ids and a confidence level.
5. **Checks every answer** (these rules are code, not model judgment):
   - It drops source ids the model wasn't given, and flags answers without a valid source.
   - It flags numbers and security claims not found in the cited sources, such as "AES-512", "ISO 27001", "24/7" or "$5M". A "Yes" must also be backed for the question's own numbers and certifications.
   - It flags answers whose cited sources don't share a meaningful word with the question.
   - Anything below high confidence, or not covered by the documents, goes to review.
6. **Writes the draft into a copy of the questionnaire.** Answers go in the right cells, rows needing review turn yellow, and three helper columns are added at the right: sources, confidence and review note. Delete those before sending.

Outputs in `--out` (default `out/secq`):

| File | What it holds |
|---|---|
| `<name>_draft.xlsx` | The questionnaire with draft answers; yellow rows need review |
| `review.csv` | Every question, flagged ones first, with answer, sources and the reason for review |
| `run_summary.json` | Counts (drafted, needs review, not covered), knowledge-base files read or skipped, requests, tokens, cost |

Model replies are cached in `out/.../cache/`. A run stopped by the daily free limit continues where it left off, and re-running after fixing the documents only redoes what changed.

## Try it on the example

The example uses a made-up company, "Northwind Analytics", with 4 documents and a 10-question sample questionnaire:

```bash
pip install -e ".[dev]"                     # once; adds openpyxl, pypdf, python-docx
python -m secq inspect --questionnaire examples/secq/sample_questionnaire.xlsx
python -m secq run --kb examples/secq/kb --questionnaire examples/secq/sample_questionnaire.xlsx \
    --company "Northwind Analytics" --out out/secq/northwind
open out/secq/northwind/sample_questionnaire_draft.xlsx
```

Expected result: 7 confident answers, plus 3 flagged questions. ISO 27001 and cyber insurance are not in the documents, so a person has to answer them.

## Using it for a client

- **Put the client's documents in one folder:**
  - SOC 2 report
  - security, access, incident-response and retention policies
  - any past answered questionnaires, which are the best source of approved wording
- **If columns aren't detected,** run `inspect`, then pass `--question-col B --answer-col C --comment-col D --header-row 4`.
- **Test on a few questions first** with `--limit 10`. Portals such as OneTrust and Whistic need their questions exported to Excel first.

## Model and privacy (read before using client documents)

- **Settings:** it uses the same `OPENROUTER_API_KEY` and model settings as leadagent. `SECQ_MODEL`, `SECQ_FALLBACK_MODELS` and `SECQ_RPM` override them.
- **Never use free models with a client's real documents.** Free-model providers may log prompts, so they're for the example data only, and the tool prints a warning when a free model is selected.
- **For client work:** set `SECQ_MODEL` to a paid model and turn on zero data retention in your OpenRouter privacy settings.
- **Cost:** a 250-question questionnaire is about 32 requests of about 10k tokens each, a few dollars at most on a mid-priced paid model.
- **Every draft needs human review before it goes to the client's customer.** The client confirms it; you don't submit it for them.

## Not built yet

- Word (`.docx`) questionnaires; export them to Excel for now
- Portal automation (OneTrust, Whistic, and similar)
- A check for answers that contradict each other across the questionnaire
- Saving reviewed answers back into the library automatically. For now, put the reviewed questionnaire into the knowledge-base folder.
