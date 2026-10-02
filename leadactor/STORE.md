# AI Lead Scorer

Give it a list of company websites and a plain-English description of who you sell to. For each company it:

- **reads the website**: the homepage plus the careers, news, about and customer pages when they're linked
- **scores the fit from 0 to 10** against your description, judging industry, size, funding stage, country and exclusions one by one
- **finds reasons to reach out now**, such as open sales roles, a funding round, a launch or a new leader, each quoted from the page it came from, with its date and URL
- **writes one opening line** for each company that fits, about one verified fact

You pay only for companies whose website was read and scored.

## Why trust the scores

The AI drafts. Plain code then checks what it wrote before you see it:

- A signal is shown only if its quote is actually on the page it cites. Numbers and funding rounds must match the page exactly.
- Anything dated more than 12 months ago is marked old and never used as the reason to reach out now.
- A company that clearly fails one of your criteria, is on your exclude list, or has been acquired or shut down scores 4 or less, whatever the AI said.
- Opening lines are rejected and rewritten if they are too long, use a cliché ("hope this finds you well", "I came across"), copy the website word for word, or leave a placeholder.

## Input

| Field | What to put |
|---|---|
| Company websites | One domain or URL per line, e.g. `acme.com` |
| Or a dataset | The dataset of another Actor run, such as Google Maps Scraper results. Each item's `website` (or `domain`, or `url`) is scored. Employee count, industry, country and funding fields are passed along as facts. |
| Who you sell to | Your ideal customer in plain words |
| Industries, employees, funding stages, countries, exclude | Optional hard criteria |
| Reasons to reach out now | What makes a company worth contacting today; sensible defaults are filled in |
| What you sell | One line, so opening lines stay relevant without pitching |

Websites rarely say how many people work there or how they are funded. If you set employees or funding stages and neither can be found, the company stays one point below your minimum score, so you can check it by hand. Turn off "Hold back unknown size or stage" to skip this, or pass a dataset that has employee counts.

## Output

One row per company scored:

```json
{
  "website": "acme.com",
  "companyName": "Acme",
  "qualified": true,
  "score": 8,
  "whyNow": "Hiring a Head of Growth (2026-08)",
  "whyNowSource": "https://acme.com/careers",
  "openingLine": "Your new Head of Growth role usually means outbound pipeline is next on the list.",
  "openingLineStatus": "ok",
  "summary": "Payroll software for dental clinics.",
  "criteria": [{"name": "industry", "status": "met", "evidence": "B2B software for clinics"}],
  "signals": [{"type": "hiring", "headline": "Hiring a Head of Growth", "evidence": "hiring a Head of Growth to lead marketing", "sourceUrl": "https://acme.com/careers", "date": "2026-08", "old": false}],
  "scoreReasons": ["B2B software", "hiring its first growth lead"],
  "disqualifiers": [],
  "pagesRead": ["https://acme.com/", "https://acme.com/careers"],
  "listData": {}
}
```

- `qualified` is true when the company fits and scores at least your minimum (6 by default).
- `openingLineStatus` is `ok`, `needs_edit` (it still failed a check after one rewrite, so edit it before sending) or `not_written`.

The run's `OUTPUT` record lists every company that was **not** scored, with the reason: website offline or blocking readers, not a company website (for example a LinkedIn or Google Maps link), or a temporary error. These are free.

## Pricing

Pay per event: one charge per company scored, whether or not it qualifies. There is no charge for websites that can't be read, errors, or rows without a usable website. The run stops before it would go over your maximum cost per run.

## Good to know

- It reads each company's own pages only, honours `robots.txt`, and reads at most 6 pages per company.
- Sites that only render with JavaScript show little text, so they tend to score low. Check low scores on well-known companies by hand.
- It does not find people or email addresses. Pair it with a contact-finding Actor for the companies that qualify.
