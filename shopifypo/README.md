# shopifypo: wholesale purchase orders into Shopify draft orders

Reads the purchase orders that wholesale buyers email to a Shopify brand (PDF, Excel, Word, CSV or typed in the email) and turns each one into a checked draft order. Every line is matched to the brand's catalog and price list in code, and anything that doesn't add up is listed for a person. It is idea #7 from the business analysis in [`analysis/`](../analysis/): a Shopify app for merchants selling wholesale, at about $99 a month.

> **Status (2026-10-11):** built and tested offline on a synthetic inbox with a stand-in model. It has not run on a real model or a real store yet. `push` was tested against a fake Shopify API only, so try it on a development store first.

## What it does

1. **Reads the inbox:** a folder of `.eml` or `.txt` emails, with PDF, Excel, Word and CSV attachments read as text.
2. **Reads each PO with the model** (1 request per email): PO number, buyer, ship-to address, ship date, terms, and every line's SKU, buyer item number, barcode, description, quantity, unit and price, copied as written. It also says whether the email is a new PO, a "same as last time" reorder, a change to an order already placed, or something else.
3. **Matches every line to the catalog in code** (the model never chooses the product):
   - by the brand's SKU, then the buyer's own item number (from the price list), then the UPC/EAN barcode, then a SKU written in the description
   - last, by description: "Cedar Sage 12oz candle" means Cedar & Sage Soy Candle, 12 oz. A description that fits two variants ("Sea Salt Linen candle", without a size) is never guessed; the person picks.
4. **Checks the numbers** (these rules are code, not model judgment):
   - Every quantity, price, SKU, PO number and line total must appear in the email. A misread is held, and an invented SKU is ignored.
   - Prices are compared with the buyer's own price list, then the general one, then retail less the wholesale discount. A PO price more than 1% off is held for the merchant to decide.
   - The PO's own arithmetic is checked: quantity × price = line total, and the lines add up to the subtotal.
   - Quantities must be whole cases (case pack) and above the minimum. Cases are converted to units.
   - Archived or draft products, short stock, a buyer who isn't a Shopify customer yet, a PO number already entered (this run or earlier, via `po_log.csv`), a ship date that's very soon, and a ship-to ZIP code in the wrong state are all flagged.
5. **Writes draft orders** for the POs with nothing to fix, and `push` creates them in Shopify.

Outputs in `--out` (default `out/shopifypo`):

| File | What it holds |
|---|---|
| `review.csv` | What to fix first (prices, ambiguous items, duplicates, new buyers), then things to know (stock, prices filled in from the list) |
| `orders.xlsx` | Every PO and every line: what the PO says, the matched SKU, how it was matched, PO price, expected price and checks; rows to fix in yellow |
| `draft_orders/*.json` | One file per ready order: SKUs, quantities, wholesale prices, ship-to, PO number and terms |
| `po_log.csv` | Every PO entered so far. Pass it as `--seen` next time so a resent PO isn't entered twice |
| `orders.json`, `run_summary.json` | Machine-readable results, requests, tokens and cost |

Model replies are cached in `out/.../cache/`. A run stopped by the daily free limit continues where it left off.

## Try it on the example

`examples/shopifypo/` holds a made-up inbox for a fictional brand, "Juniper & Pine Candle Co.":
- `catalog.csv`: a Shopify product export with 12 variants, one archived, plus an extra image row
- `prices.csv`: a wholesale price list with case packs, one buyer's special prices and another buyer's item numbers
- `customers.csv`: a Shopify customer export
- `shop.toml`: the brand's settings
- `truth.json`: the answer key that `score` compares a run against

The 7 emails:
1. a clean PDF PO (ready)
2. an Excel PO using the buyer's own item numbers and a barcode, with one line missing its size
3. a PO typed in the email with a quantity that isn't a whole case
4. a PDF PO with a wrong price and a line total that doesn't add up
5. the first PO sent again
6. a first order from a new buyer with an archived product and a SKU the brand doesn't sell
7. "same as our last order"

```bash
python -m shopifypo inbox --emails examples/shopifypo/inbox
python -m shopifypo run --emails examples/shopifypo/inbox --catalog examples/shopifypo/catalog.csv --prices examples/shopifypo/prices.csv --customers examples/shopifypo/customers.csv --shop examples/shopifypo/shop.toml --out out/shopifypo/sample
python -m shopifypo score --run out/shopifypo/sample
open out/shopifypo/sample/orders.xlsx
```

The run takes 7 model requests. With a perfect model, `score` shows all 18 lines matched with the right prices, 1 order ready and 6 held as expected, and 11 of 11 expected checks with no other must-fix checks.

## Putting orders into Shopify

1. In the store's admin, create a custom app (Settings > Apps and sales channels > Develop apps). Give it the Admin API scopes `read_products` and `write_draft_orders`, install it, and copy the Admin API access token.
2. Put the token in `.env` as `SHOPIFY_ACCESS_TOKEN=...` (never in a file you commit), and the store's `your-store.myshopify.com` in `shop.toml` as `shop_domain`.
3. See what would be created, then create it:

```bash
python -m shopifypo push --run out/shopifypo/sample --shop examples/shopifypo/shop.toml
python -m shopifypo push --run out/shopifypo/sample --shop examples/shopifypo/shop.toml --yes
```

`push` looks up each SKU in the store. A wholesale price below the store price becomes a percentage discount on that line. An order with a price above the store price, or a SKU the store doesn't have, is skipped. After creating a draft, it compares Shopify's subtotal with the expected one and lists any difference in `pushed.csv`. Drafts aren't charged or sent; the merchant reviews them in Shopify and sends the invoice.

## Using it for a merchant

- **What to ask for:** 20-30 recent wholesale POs (forward them, or drag them out of the mail app as `.eml`), the product export, the wholesale price list with case packs, and the customer export. If no merchant will share POs, the idea fails the first test.
- **Show the result:** enter 5 of their real POs and show the time saved and the mistakes caught (prices, arithmetic, duplicates).
- **Time your checking:** the model assumes about 30 minutes a month per merchant.

## Model and privacy (read before using a merchant's orders)

- **Settings:** it uses the same `OPENROUTER_API_KEY` and model settings as leadagent. `SHOPIFYPO_MODEL`, `SHOPIFYPO_FALLBACK_MODELS`, `SHOPIFYPO_RPM` and `SHOPIFYPO_BASE_URL` override them.
- **Use free models only with the example.** Free-model providers may log prompts, and POs carry buyers' names, addresses and prices. For a merchant, set `SHOPIFYPO_MODEL` to a paid model and turn on zero data retention in your OpenRouter privacy settings.
- **Cost:** a 3-page PO is about 8k tokens, a few cents on a mid-priced paid model. That's $2-4 a month at 60 POs against $99 of revenue.

## Not built yet

- The Shopify app itself (OAuth install, an embedded admin page, App Store listing); for now it runs with a custom app's token
- Watching a shared inbox; for now emails go through a folder
- Shopify B2B (Plus) company locations and catalogs; drafts use the buyer's email and line discounts
- Finding "same as last order" in Shopify's order history; for now it's flagged for a person
- Scanned (image-only) PDF POs; they need text recognition first

## Tests

```bash
python -m pytest -q tests/test_shopifypo.py
```

The tests use a fake model that answers from the answer key, deliberate model mistakes the checks must catch, and a fake Shopify API. They need no network, API key or store.
