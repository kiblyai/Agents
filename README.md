# Agents

AI agents for the service businesses ranked in [`analysis/`](analysis/): the unit-economics model, launch plans and sources.

| Agent | What it does | Status | Docs |
|---|---|---|---|
| `leadagent` | Turns a list of companies into scored, cited leads with an opening line | Parked: built and tuned on real runs; next step is a real Apollo list | [leadagent/README.md](leadagent/README.md) |
| `secq` | Drafts security-questionnaire answers from a company's own documents, citing each source and flagging what needs review | Built; tested on example data | [secq/README.md](secq/README.md) |
| `medchron` | Builds a cited medical chronology from a personal-injury case's records: visits by date, treatment gaps, pre-injury records, bill totals and a review queue | Built; tested on a synthetic case; synthetic records only until the HIPAA steps are done | [medchron/README.md](medchron/README.md) |
| `leadactor` | `leadagent` packaged as a pay-per-event Apify Store Actor ("AI Lead Scorer"): users pay per company scored | Built; tested offline, including Apify's billing; next step is a paid-model test, then publishing | [leadactor/README.md](leadactor/README.md) |
| `freightq` | Reads a freight brokerage's quote requests and load tenders, prices each lane from the brokerage's own past loads, drafts the replies and writes tenders as TMS rows | Built; tested on a synthetic inbox | [freightq/README.md](freightq/README.md) |
| `outreach` | Runs a pay-per-meeting cold-email service: screens leads against the client's meeting bar, writes checked 3-email sequences, sorts replies (opt-outs and bounces by rules) and works out the monthly invoice | Built; tested on a synthetic campaign | [outreach/README.md](outreach/README.md) |
| `shopifypo` | Turns wholesale purchase orders emailed to a Shopify brand into checked draft orders: every line matched to the catalog and price list, with price, case-pack, stock and duplicate checks | Built; tested on a synthetic inbox and a fake Shopify API | [shopifypo/README.md](shopifypo/README.md) |
| `grants` | Finds which funders are worth a small nonprofit's time and drafts the letter of inquiry from the nonprofit's own documents, every fact traced to its source | Built; tested on a synthetic nonprofit | [grants/README.md](grants/README.md) |
| `txcoord` | Reads a real-estate contract and its addenda and works out every deadline in code (business days, holidays), with a calendar, checklist and emails to the parties | Built; tested on synthetic contracts | [txcoord/README.md](txcoord/README.md) |
| `crmclean` | Cleans a HubSpot portal (duplicates, formats, missing links, stale leads) and fills in company details from each company's own website; nothing changes until a person approves | Built; tested on a synthetic portal and a fake HubSpot API | [crmclean/README.md](crmclean/README.md) |
| `govbids` | Picks the SAM.gov notices worth bidding on for a small federal contractor and turns an RFP into a compliance matrix and key facts | Built; tested on synthetic notices, a synthetic RFP and a fake SAM.gov API | [govbids/README.md](govbids/README.md) |
| `upworkbid` | Decides which Upwork jobs to bid on, with the rate after fees worked out in code, and drafts checked proposals to paste into Upwork | Built; tested on synthetic job posts | [upworkbid/README.md](upworkbid/README.md) |

All of them share `agentkit/`, which holds the model client (OpenRouter by default, with rate limiting, retries, backup models and JSON repair), per-agent settings, a cached request runner, document readers, search, table writers and the shared checks (quotes, numbers, dates), plus `.env` loading and a small PDF writer for the synthetic examples.

Every agent except leadagent, leadactor and secq has a synthetic example with an answer key: run it, then `python -m <agent> score` shows how close the run came. Each README's "Try it on the example" section has the commands.

## Setup (once)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # then put your OPENROUTER_API_KEY in .env (git-ignored)
python -m pytest -q         # all tests run offline with fake models and websites
```

Keys live only in environment variables or the git-ignored `.env`, never in code or output files.
