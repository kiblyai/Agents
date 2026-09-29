# AI-agent business ideas: unit-economics model

`model.py` holds every assumption and computes every metric. `results.md` is its output: base, conservative and optimistic comparison tables, scenario spread, category rankings, overall ranking, month-by-month cash flow for all 12 modeled ideas, and a sensitivity check. `SOURCES.md` lists the sources, with dates.

```
python3 analysis/model.py            # rewrites analysis/results.md
python3 analysis/model.py --stdout   # prints it instead
```

## Definitions as implemented

- **Owner time**: $101/h, capped at 20 h/wk (86.7 h/month). When a service hits the cap, new-customer intake is scaled down (flagged "Capacity-capped").
- **Gross profit per customer-month** = revenue − (LLM tokens + per-customer hosting/third-party APIs + payment processing + % marketplace take-rate + delivery/support hours × $101).
- **Cash CAC** = acquisition cash (sending stack, data, Connects, listing fees) ÷ customers acquired in 12 months. **Loaded CAC** adds acquisition hours × $101.
- **LTV** = monthly gross profit ÷ monthly churn, plus setup-fee gross profit, minus onboarding hours × $101 when there is no setup fee. Project work: gross profit per project × (1 + expected repeat projects).
- **Payback** = Loaded CAC ÷ monthly gross profit.
- **Cash break-even**: the first month after which cumulative cash (revenue − all cash costs) never goes negative again. **True break-even**: the same test on cumulative (cash − all hours × $101). A `*` means it lands after month 12. It is found by running the same plan for 36 months, not by straight-line extrapolation.
- **ROI**: cash ROI = 12-month cash net ÷ non-variable cash invested (tools, hosting, insurance, acquisition). Time-adjusted ROI = 12-month true profit ÷ (that cash + non-delivery hours × $101). Both apply the brief's "(net − investment) ÷ investment" without counting the investment twice.
- **Effective $/h** = cumulative cash net ÷ cumulative hours at months 3, 6 and 12, plus the month-12 run-rate.
- **Min. cash needed** = the larger of month-1 spend and the deepest dip in cumulative cash.
- Customer counts are expected values, so they can be fractional.

## What to change first

Each idea's `Scenario(...)` lines contain the handful of inputs that drive everything: `pos_rate` (positive replies per contact), `close_rate`, `arpu`, `churn`, and `delivery_h`. `WEIGHTS` near the bottom controls the overall ranking.
