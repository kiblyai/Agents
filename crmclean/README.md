# crmclean: HubSpot cleanup and company enrichment

Cleans a small company's HubSpot contacts and companies, and fills in missing company details from each company's own website. Nothing changes in HubSpot until a person approves it. It is idea #10 from the business analysis in [`analysis/`](../analysis/): a HubSpot Marketplace app at about $79 a month.

> **Status (2026-10-11):** built and tested offline on a synthetic portal with a stand-in model and a fake HubSpot API. It has not run on a real model or a real portal yet.

## What it does

1. **Reads HubSpot's exports** (Contacts > Export and Companies > Export, CSV or Excel), keyed by Record ID, so every change can be matched back.
2. **Applies the rules in code** (no model, no cost). `check` runs only this step.
   - **Duplicates to merge:** contacts with the same email (ignoring case and spaces) and companies with the same domain. The record to keep is the one with the most recent activity.
   - **Possible duplicates, for a person to judge:** same name at the same company with different emails, and companies with the same name but different or missing domains.
   - **Fixes:**
     - emails with spaces or capitals
     - names in ALL CAPS or all lower case (with McDonald and O'Brien handled)
     - company names in capitals (LLC stays LLC, INC becomes Inc)
     - US phone numbers in any format, as +1 555-201-3344; international numbers are left alone
     - domains with https://, www or a path
     - state names or codes, as set in `crm.toml`
     - "USA" and the like, as "United States"
   - **Links:** a contact whose email domain matches a company is linked to it. A business domain with no company record is flagged.
   - **Flags:** invalid emails, shared inboxes (info@, sales@), phone numbers that can't be numbers, and leads with no activity for 18 months.
3. **Fills in missing company details** (`run`, 1 request per company): industry, a one-sentence description, staff count, city and state, read from the company's homepage (saved pages with `--sites`, or live with `--fetch`). Every value needs a quote from the page that code finds there:
   - the industry must be one of the portal's labels
   - a staff count must be a number in a sentence about staff ("since 1987" never becomes 1,987 employees)
   - a description can't add numbers the page doesn't have
   - fields that already have a value are never overwritten
4. **Waits for approval.** Fixes by rule come pre-approved. Values from websites need a "yes", and so does every merge, because a merge can't be undone in HubSpot. `apply` then writes HubSpot import files, or sends the approved rows through the API with `--yes` (merges first, then updates, then links).

## Outputs

| File | What it holds |
|---|---|
| `changes.csv` | Every proposed change: record, field, old and new value, reason, and for website values the page and quote. Column `approve`: "yes" to send, anything else to skip |
| `merges.csv` | Duplicate pairs: which record to keep and which to merge into it. Approve each one |
| `review.csv` | What a person should look at: invalid emails, possible duplicates, shared inboxes, missing companies, bad phone numbers, stale leads |
| `cleanup.xlsx` | All three in one workbook; rows waiting for approval in yellow |
| `import_contacts.csv`, `import_companies.csv`, `associations.csv` | From `apply`: approved changes as HubSpot import files (update existing records by Record ID) and contact-company links |
| `results.json`, `run_summary.json` | Machine-readable results, requests and cost |

## Try it on the example

`examples/crmclean/` holds a made-up portal, "Pinecrest Marketing":
- `companies.csv`: 10 companies, including a duplicate by domain, names in capitals, mixed state and country formats, and a same-name pair with one domain missing
- `contacts.csv`: 12 contacts, including a duplicate by email, an email with a trailing space, names in capitals and lower case, a bad address, a shared inbox, a bad phone number, a stale lead, a missing company link and an unknown company
- `sites/`: saved homepages for the 4 companies to fill in
- `crm.toml`: the settings
- `truth.json`: the answer key

```bash
python -m crmclean check --contacts examples/crmclean/contacts.csv --companies examples/crmclean/companies.csv --crm examples/crmclean/crm.toml --today 2026-10-12 --out out/crmclean/sample
python -m crmclean run --contacts examples/crmclean/contacts.csv --companies examples/crmclean/companies.csv --crm examples/crmclean/crm.toml --today 2026-10-12 --sites examples/crmclean/sites --out out/crmclean/sample
python -m crmclean score --run out/crmclean/sample
open out/crmclean/sample/cleanup.xlsx
python -m crmclean apply --run out/crmclean/sample
```

`check` makes no model requests; `run` makes 4. With a perfect model, `score` shows 36 of 36 expected changes (20 by rule, 16 from websites), both duplicates, and 9 of 9 problems flagged, with nothing extra.

## Using it for a client

1. Export contacts and companies from HubSpot (all properties, or at least the ones in the example) and run `check` first. It's free, and the review list is a good opening for the sales call.
2. Run `run --fetch` to fill in companies from their live websites (it reads the homepage and one more page, and honors robots.txt). Add `--limit 20` for a first look.
3. Go through `changes.csv` and `merges.csv`, write "yes" in `approve` for what should happen, then run `apply --run ...`. Import the files it writes (Contacts or Companies > Import > update existing records), or create a private app (Settings > Integrations > Private apps) with the CRM object read and write scopes, put its token in `.env` as `HUBSPOT_ACCESS_TOKEN`, and run `apply --run ... --yes`.
4. **Time it:** the model assumes about 30 minutes a month per portal after the first cleanup.

## Model and privacy (read before using a client's export)

- **Settings:** it uses the same `OPENROUTER_API_KEY` and model settings as leadagent. `CRMCLEAN_MODEL`, `CRMCLEAN_FALLBACK_MODELS`, `CRMCLEAN_RPM` and `CRMCLEAN_BASE_URL` override them.
- **Only company websites go to the model.** Contacts never leave the machine: the rules run in code. Still use a paid model with zero data retention for client work (set `CRMCLEAN_MODEL`), since prompts name the client's accounts.
- **Exports hold personal data.** Keep them out of the repository (the `out/` folder is git-ignored) and delete them when the job is done.
- **Cost:** about 3k tokens per company filled in; under $1 for a few hundred companies on a cheap paid model.

## Not built yet

- The Marketplace app itself (OAuth install, scheduled runs, a settings page); for now it runs on exports and a private app token
- Fuzzy duplicate names ("Acme Analytics" vs "Acme Analytic Group"); exact names and domains only
- Deals and tickets; contacts and companies only
- Email verification beyond the syntax check (a paid verifier, as in leadagent)

## Tests

```bash
python -m pytest -q tests/test_crmclean.py
```

The tests use a fake model that answers from the answer key, a model that over-reaches, and a fake HubSpot API. They need no network, API key or portal.
