"""
Unit-economics model for AI-agent business ideas (solo founder, no distribution).

Run:  python3 analysis/model.py            -> writes analysis/results.md
      python3 analysis/model.py --stdout   -> prints the report instead

Everything that matters is a parameter near the top of this file or inside
IDEAS. Change a number, re-run, and every metric, table and ranking updates.

Conventions
-----------
* Money in USD, time in hours, one month = 20 h/wk * 52 / 12 = 86.7 h cap.
* Customer counts are expected values (fractional), not literal people.
* "delivery hours" (serving a customer) are valued at HOURLY and sit inside
  gross profit, exactly as the brief defines it (support hours x $101).
* Build, acquisition, maintenance and admin hours are the owner-time
  "investment" and are only charged in true profit / time-adjusted ROI.
* Percentage take-rates charged on every dollar (Upwork 10%, Apify 20%) are a
  cost of revenue; per-proposal / listing charges are acquisition cash.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

HOURLY = 101.0
HOURS_CAP = 20 * 52 / 12  # 86.67 h per month
MONTHS = 12

# --- LLM prices, USD per million tokens [Sourced: platform.claude.com/docs/en/about-claude/pricing, read 2026-09-29]
PRICES = {
    "haiku-4.5": (1.00, 5.00),
    "sonnet-5.5": (2.00, 10.00),
    "opus-5.5": (4.00, 20.00),
}
WEB_SEARCH_PER_CALL = 10 / 1000  # $10 per 1,000 searches [Sourced, same page]
TOKEN_OVERHEAD = 2.0  # [Assumption] retries, tool-call loops, prompt bloat: double the clean estimate


def llm(model: str, tokens_in: float, tokens_out: float, searches: float = 0) -> float:
    pin, pout = PRICES[model]
    return (tokens_in * pin + tokens_out * pout) / 1e6 + searches * WEB_SEARCH_PER_CALL


# --- Unit token costs used below (clean estimate x TOKEN_OVERHEAD) ------------
# Security questionnaire: per question, retrieval-grounded draft on Sonnet + Haiku check.
SECQ_PER_QUESTION = (llm("sonnet-5.5", 5000, 400) + llm("haiku-4.5", 3000, 100)) * TOKEN_OVERHEAD
SECQ_PER_QUESTIONNAIRE = SECQ_PER_QUESTION * 250  # [Assumption] 250 questions average
# Medical chronology: per source page, Haiku vision OCR + Sonnet extraction + share of synthesis.
MEDCHRON_PER_PAGE = (llm("haiku-4.5", 1600, 700) + llm("sonnet-5.5", 2000, 350) + llm("sonnet-5.5", 500, 100)) * TOKEN_OVERHEAD
# Account research: 1 web search + ~6k tokens of fetched pages summarised by Haiku.
RESEARCH_PER_CONTACT = llm("haiku-4.5", 6000, 400, searches=1) * TOKEN_OVERHEAD
EMAIL_FIND_VERIFY_PER_CONTACT = 0.04  # [Estimate] pay-per-use email finder + verifier
# Freight: per inbound email, Sonnet parse + draft.
FREIGHT_PER_EMAIL = llm("sonnet-5.5", 3000, 400) * TOKEN_OVERHEAD
# Shopify B2B purchase order: 3-page PDF + catalogue candidates + draft order JSON on Sonnet.
PO_PER_ORDER = llm("sonnet-5.5", 8000, 1000) * TOKEN_OVERHEAD
# GovCon: ~50 notices/day scored on Haiku + 2 proposal shreds/drafts per month on Sonnet.
GOVCON_PER_MONTH = (30 * 50 * llm("haiku-4.5", 3000, 150) + 2 * llm("sonnet-5.5", 200_000, 30_000)) * TOKEN_OVERHEAD
# Grants: 50 funder look-ups + 2 drafts x 3 iterations.
GRANTS_PER_MONTH = (50 * llm("haiku-4.5", 6000, 400, searches=1) + 6 * llm("sonnet-5.5", 60_000, 8000)) * TOKEN_OVERHEAD

# --- Payment processing -----------------------------------------------------
# Stripe 2.9% + $0.30, +1.5% on non-domestic cards [Sourced]; assume 30% non-domestic -> +0.45%.
# Subscriptions add Stripe Billing 0.7% [Sourced].
PROC_SERVICE = (0.0335, 0.30)
PROC_SUBSCRIPTION = (0.0405, 0.30)
PROC_SHOPIFY = (0.029, 0.0)  # Shopify billing API: 2.9% processing, 0% rev share < $1M [Sourced]

# --- Shared cold-email stack, per month for ~1,500 new contacts ----------------
# 6 Google Workspace inboxes x $8.40 [Sourced] + Instantly Growth $37 [Sourced]
# + Apollo Basic monthly $59 [Sourced] + verification ~1,500 x $0.005 [Estimate]
COLD_EMAIL_STACK = 6 * 8.40 + 37 + 59 + 1500 * 0.005
DOMAINS_ONEOFF = 3 * 12  # [Assumption] 3 .com domains ~ $12/yr each, bought up front
HOSTING_BASE = 7 + 25  # Render starter $7 + Supabase Pro $25 [Sourced]
TECH_EO_INSURANCE = 75  # tech E&O ~ $65-75/mo for IT consultants [Sourced: Insureon]


@dataclass
class Scenario:
    # revenue
    arpu: float = 0.0  # recurring revenue per billed customer-month
    first_month_factor: float = 1.0  # share of ARPU billed in a customer's first month
    setup_fee: float = 0.0  # one-off per new customer (project price for project mode)
    take_rate: float = 0.0
    proc: tuple = PROC_SERVICE
    # cost to serve
    var_cash: float = 0.0  # tokens + per-customer hosting + third-party APIs, per billed month
    var_cash_oneoff: float = 0.0  # per new customer (e.g. tokens for a project)
    delivery_h: float = 0.0  # per billed customer-month
    onboard_h: float = 0.0  # per new customer, in month acquired (setup / project delivery)
    churn: float = 0.05
    repeat_rate: float = 0.0  # project mode: share of projects that yield a repeat project 2 months later
    lifetime_repeats: float = 0.0  # project mode: repeat purchases per client for LTV
    # acquisition: either a cold-email funnel ...
    contacts: float = 0.0
    pos_rate: float = 0.0
    reply_to_call: float = 0.6
    close_rate: float = 0.0
    call_h: float = 1.5
    pilot_rate: float = 0.0  # free pilots per call
    pilot_h: float = 0.0
    # ... or an explicit schedule of new customers per month
    new_sched: list | None = None
    ramp: list = field(default_factory=lambda: [0.0, 0.6] + [1.0] * 10)
    acq_fixed_h: float = 8.0
    acq_cash: float = 0.0  # per month once acquisition starts
    acq_start: int = 1
    # fixed
    build_h: list = field(default_factory=list)  # by month, starting month 1
    maint_h: float = 4.0
    admin_h: float = 4.0
    fixed_cash: float = HOSTING_BASE
    oneoff_cash: dict = field(default_factory=dict)  # month -> amount (non-acquisition)
    acq_oneoff_cash: dict = field(default_factory=dict)  # month -> amount (acquisition)
    mode: str = "recurring"  # or "project"

    def new_customers_planned(self, m: int) -> float:
        # months beyond the 12-month schedule repeat the month-12 value
        if self.new_sched is not None:
            return self.new_sched[min(m, len(self.new_sched)) - 1]
        steady = self.contacts * self.pos_rate * self.reply_to_call * self.close_rate
        return steady * self.ramp[min(m, len(self.ramp)) - 1]

    def calls_planned(self, m: int) -> float:
        if self.new_sched is not None:
            return 0.0
        return self.contacts * self.pos_rate * self.reply_to_call * self.ramp[min(m, len(self.ramp)) - 1]


@dataclass
class Idea:
    key: str
    name: str
    customer: str
    model: str
    price_label: dict  # scenario -> text
    channel: str
    risk: str
    kill: str
    confidence: str
    scenarios: dict  # "cons"/"base"/"opt" -> Scenario


# ---------------------------------------------------------------------------
# Ideas (survivors of the constraint filter). Every number is tagged in
# comments: [Sourced] (see SOURCES.md), [Estimate] (reasoning given), [Assumption].
# ---------------------------------------------------------------------------

def cold(**kw) -> dict:
    """Defaults shared by every cold-email-sold idea."""
    base = dict(contacts=1500, acq_cash=COLD_EMAIL_STACK, acq_oneoff_cash={1: DOMAINS_ONEOFF})
    base.update(kw)
    return base


IDEAS: list[Idea] = []

# 1. Custom agent builds on Upwork (freelance benchmark)
_up = dict(
    mode="project", proc=(0.0, 0.0), take_rate=0.10,  # Upwork ~10% freelancer fee [Sourced]
    var_cash_oneoff=40,  # [Estimate] dev/test tokens per project
    churn=1.0, acq_fixed_h=15,  # [Estimate] ~50 proposals/mo at ~18 min each
    acq_cash=50 * 16 * 0.15 + 14.99,  # 50 proposals x ~16 Connects x $0.15 [Sourced price, Estimate count] + Freelancer Plus [Sourced]
    build_h=[20], maint_h=0, admin_h=3, fixed_cash=0,
    oneoff_cash={1: 50},
)
IDEAS.append(Idea(
    key="upwork", name="Custom agent builds via Upwork", customer="SMBs/startups posting AI-agent jobs",
    model="Freelance projects", price_label={"cons": "$1.8k/project", "base": "$2.5k/project", "opt": "$4k/project"},
    channel="Upwork proposals (50/mo)",
    risk="Global rate competition (AI postings P75 ~$40/h) keeps effective rate under $101/h; no compounding asset",
    kill="<2 interviews from the first 40 proposals, or no offer that works out >= $90/h after fees",
    confidence="H",
    scenarios={
        # 50 proposals/mo x win rate [Sourced: 3-7% normal, 1-2% templated, up to 10% personalised]
        "cons": Scenario(**_up, setup_fee=1800, onboard_h=32, new_sched=[50 * 0.02 * r for r in [0.25, 0.6, 0.85] + [1] * 9], repeat_rate=0.10, lifetime_repeats=0.25),
        "base": Scenario(**_up, setup_fee=2500, onboard_h=28, new_sched=[50 * 0.04 * r for r in [0.25, 0.6, 0.85] + [1] * 9], repeat_rate=0.15, lifetime_repeats=0.40),
        "opt": Scenario(**_up, setup_fee=4000, onboard_h=24, new_sched=[50 * 0.07 * r for r in [0.25, 0.6, 0.85] + [1] * 9], repeat_rate=0.20, lifetime_repeats=0.60),
    },
))

# 2. Security-questionnaire response service for B2B SaaS startups
_sq = cold(
    build_h=[45, 5], maint_h=6, fixed_cash=HOSTING_BASE + TECH_EO_INSURANCE, oneoff_cash={1: 100},
    onboard_h=5,  # [Estimate] ingest policies, SOC2 report, past answers
)
IDEAS.append(Idea(
    key="secq", name="Security-questionnaire response service", customer="Seed-Series B B2B SaaS selling to enterprises",
    model="Productized service, per questionnaire", price_label={"cons": "$450 x1/mo", "base": "$500 x1.5/mo", "opt": "$600 x2/mo"},
    channel="Cold email to founders/CTOs with trust pages or enterprise hiring signals",
    risk="Commoditized by Vanta/Drata/Conveyor AI answering; liability if a drafted answer is wrong (client signs off)",
    kill="From 1,000 contacts: <3 positive replies and 0 paid questionnaires (even at a $250 intro price) by day 30",
    confidence="M",
    scenarios={
        "cons": Scenario(**_sq, arpu=450 * 1.0, var_cash=SECQ_PER_QUESTIONNAIRE * 1.0 + 2, delivery_h=2.5 * 1.0, churn=0.12, pos_rate=0.003, close_rate=0.15),
        "base": Scenario(**_sq, arpu=500 * 1.5, var_cash=SECQ_PER_QUESTIONNAIRE * 1.5 + 2, delivery_h=1.5 * 1.5, churn=0.08, pos_rate=0.005, close_rate=0.25),
        "opt": Scenario(**_sq, arpu=600 * 2.0, var_cash=SECQ_PER_QUESTIONNAIRE * 2.0 + 2, delivery_h=1.0 * 2.0, churn=0.05, pos_rate=0.009, close_rate=0.35),
    },
))

# 3. Medical-chronology service for personal-injury firms (market $1.50-$4.00/page [Sourced])
_mc = cold(
    build_h=[55, 5], maint_h=6, fixed_cash=50 + TECH_EO_INSURANCE,  # AWS/Bedrock under a BAA [Estimate $50]
    oneoff_cash={1: 150}, onboard_h=1.0, pilot_rate=1.0, call_h=1.0,
)
IDEAS.append(Idea(
    key="medchron", name="Medical-chronology service for PI law firms", customer="US plaintiff personal-injury firms (1-20 attorneys)",
    model="Productized service, per page", price_label={"cons": "$0.80/pg x700", "base": "$0.90/pg x1,200", "opt": "$1.00/pg x2,000"},
    channel="Cold email + first file free (proof-first)",
    risk="Handling medical records (PHI/security expectations) + errors in a litigation work product; funded AI rivals (EvenUp, Supio) pressuring price",
    kill="<2 firms accept a free pilot from 1,000 contacts, or pilot QA takes >0.006 h/page, or 0 of the first 3 pilots send a paid file",
    confidence="M",
    scenarios={
        "cons": Scenario(**_mc, arpu=700 * 0.80, var_cash=700 * MEDCHRON_PER_PAGE, delivery_h=700 * 0.006 + 1.5, churn=0.10, pos_rate=0.0025, close_rate=0.20, pilot_h=4.0),
        "base": Scenario(**_mc, arpu=1200 * 0.90, var_cash=1200 * MEDCHRON_PER_PAGE, delivery_h=1200 * 0.004 + 1.0, churn=0.06, pos_rate=0.004, close_rate=0.35, pilot_h=3.0),
        "opt": Scenario(**_mc, arpu=2000 * 1.00, var_cash=2000 * MEDCHRON_PER_PAGE, delivery_h=2000 * 0.003 + 0.75, churn=0.04, pos_rate=0.007, close_rate=0.45, pilot_h=2.5),
    },
))

# 4. Researched lead lists / GTM-engineering-lite retainer (Clay builds $2-5k/mo [Sourced])
_gl = cold(build_h=[35, 5], maint_h=6, oneoff_cash={1: 100}, onboard_h=4)
_per_contact = RESEARCH_PER_CONTACT + EMAIL_FIND_VERIFY_PER_CONTACT
IDEAS.append(Idea(
    key="gtmlists", name="Researched lead-list retainer (agent-built)", customer="B2B agencies, consultancies, seed-stage founders",
    model="Productized service, monthly", price_label={"cons": "$700/mo", "base": "$1,000/mo", "opt": "$1,500/mo"},
    channel="Cold email with a free 10-lead sample of the prospect's own ICP",
    risk="Low switching cost; Clay/Apollo ship the same agent features; data-vendor terms on resale",
    kill="<3 positive replies from 1,000 sample-first emails, or 0 paid by day 30",
    confidence="M",
    scenarios={
        "cons": Scenario(**_gl, arpu=700, var_cash=2000 * _per_contact * 1.3, delivery_h=5, churn=0.15, pos_rate=0.003, close_rate=0.15),
        "base": Scenario(**_gl, arpu=1000, var_cash=2000 * _per_contact, delivery_h=3, churn=0.10, pos_rate=0.005, close_rate=0.25),
        "opt": Scenario(**_gl, arpu=1500, var_cash=2000 * _per_contact * 0.8, delivery_h=2, churn=0.07, pos_rate=0.008, close_rate=0.35),
    },
))

# 5. Outbound meetings (hybrid: $500 platform fee + per held meeting) [Sourced: $300-600/meeting, hybrid $150-300 + retainer]
# Meetings per client are derived from the SAME reply-rate assumption used to sell the service:
# 2,000 contacts/mo x positive-reply rate x 60% book x 75% show x 80% meet the client's bar. [Estimate]
_client_contacts = 2000
_client_infra = 13 * 8.40 + 5 * 12 / 12 + 30  # 13 inboxes + 5 domains + sequencer share [Sourced prices, Estimate share]
_mt = cold(build_h=[35, 5], maint_h=6, fixed_cash=HOSTING_BASE + TECH_EO_INSURANCE, oneoff_cash={1: 100}, onboard_h=6, first_month_factor=0.25)


def _meetings(pos: float) -> float:
    return _client_contacts * pos * 0.6 * 0.75 * 0.8


IDEAS.append(Idea(
    key="meetings", name="Outbound meetings agency (pay-per-meeting hybrid)", customer="B2B service firms with ACV > $10k (MSPs, agencies, consultancies)",
    model="Outcome-based, hybrid",
    price_label={k: f"$500 + {_meetings(p):.1f} x $250" for k, p in (("cons", 0.003), ("base", 0.005), ("opt", 0.008))},
    channel="Cold email (the offer is its own proof)",
    risk="Result variance + deliverability; AI-SDR category year-one churn reported at 50-70%",
    kill="Your own campaign for this offer books <3 calls from 1,500 contacts in 30 days",
    confidence="L",
    scenarios={
        "cons": Scenario(**_mt, arpu=500 + _meetings(0.003) * 250, var_cash=_client_infra + _client_contacts * _per_contact * 1.3, delivery_h=8, churn=0.22, pos_rate=0.003, close_rate=0.12),
        "base": Scenario(**_mt, arpu=500 + _meetings(0.005) * 250, var_cash=_client_infra + _client_contacts * _per_contact, delivery_h=6, churn=0.15, pos_rate=0.005, close_rate=0.20),
        "opt": Scenario(**_mt, arpu=500 + _meetings(0.008) * 250, var_cash=_client_infra + _client_contacts * _per_contact * 0.8, delivery_h=5, churn=0.10, pos_rate=0.008, close_rate=0.30),
    },
))

# 6. Freight-broker back-office agent (setup + retainer); ~26,100 active US brokerages [Sourced]
_fr = cold(build_h=[55, 15], maint_h=8, fixed_cash=HOSTING_BASE + TECH_EO_INSURANCE, oneoff_cash={1: 150},
           acq_fixed_h=14, ramp=[0, 0, 0.7] + [1.0] * 9)  # longer cycle; includes ~20 cold calls/wk
IDEAS.append(Idea(
    key="freight", name="Freight-broker quote & order-entry agent", customer="US freight brokerages, 3-25 staff",
    model="Setup + monthly retainer", price_label={"cons": "$1.5k + $750/mo", "base": "$2.5k + $1,000/mo", "opt": "$3.5k + $1,500/mo"},
    channel="Cold email + cold calls (FMCSA broker lists are public)",
    risk="TMS integration friction (many lack APIs); phone-first buyers; brokerage failures (3,100 closed in 2024)",
    kill="<5 discovery calls from 1,500 emails + 200 dials, or no prospect will share sample quote emails",
    confidence="L",
    scenarios={
        "cons": Scenario(**_fr, arpu=750, setup_fee=1500, var_cash=3000 * FREIGHT_PER_EMAIL + 20, delivery_h=6, onboard_h=20, churn=0.08, pos_rate=0.002, close_rate=0.15),
        "base": Scenario(**_fr, arpu=1000, setup_fee=2500, var_cash=3000 * FREIGHT_PER_EMAIL + 20, delivery_h=4, onboard_h=15, churn=0.05, pos_rate=0.003, close_rate=0.20),
        "opt": Scenario(**_fr, arpu=1500, setup_fee=3500, var_cash=3000 * FREIGHT_PER_EMAIL + 20, delivery_h=3, onboard_h=12, churn=0.03, pos_rate=0.005, close_rate=0.30),
    },
))

# 7. Apify Store actors, pay-per-event. Dev keeps 80% of (revenue - platform compute) [Sourced];
#    platform pays ~$1.4M/mo across ~3,000 devs => ~$470/mo MEAN (median lower) [Sourced/derived].
_ap = dict(proc=(0.0, 0.0), take_rate=0.2 * 0.92, build_h=[35, 25], maint_h=12, admin_h=2, fixed_cash=0,
           acq_fixed_h=6, onboard_h=0, delivery_h=0, oneoff_cash={1: 50})
_ap_sched = [0, 0] + [2 + i for i in range(10)]  # paying users acquired per month [Assumption]
IDEAS.append(Idea(
    key="apify", name="Apify Store AI actors (pay-per-event)", customer="Developers/growth teams on Apify + API users",
    model="Marketplace, usage-priced", price_label={"cons": "$7/user-mo", "base": "$10/user-mo", "opt": "$15/user-mo"},
    channel="Apify Store search (built-in distribution)",
    risk="Platform dependence (Apify retired its rental model in 2026); tiny ARPU; skewed payouts",
    kill="<20 unique users run the actor in the first 30 days after listing",
    confidence="M",
    scenarios={
        # compute ~8% of revenue, LLM tokens ~25% of revenue [Estimate]
        "cons": Scenario(**_ap, arpu=7, var_cash=7 * 0.33, churn=0.15, new_sched=[x * 0.5 for x in _ap_sched]),
        "base": Scenario(**_ap, arpu=10, var_cash=10 * 0.33, churn=0.12, new_sched=_ap_sched),
        "opt": Scenario(**_ap, arpu=15, var_cash=15 * 0.33, churn=0.08, new_sched=[x * 3 for x in _ap_sched]),
    },
))

# 8. Shopify app: B2B purchase-order email/PDF -> draft order (LevelOps, AutoApprove exist [Sourced])
_sh = dict(proc=PROC_SHOPIFY, build_h=[60, 20], maint_h=10, fixed_cash=40, acq_fixed_h=8, onboard_h=0.5,
           oneoff_cash={1: 50}, acq_oneoff_cash={1: 19})  # $19 App Store registration [Sourced]
_sh_sched = [0, 0, 0.5, 1, 1.5, 2, 2, 2, 2, 2, 2, 2]  # [Assumption] organic + light outbound; median app < $1k MRR [Sourced]
IDEAS.append(Idea(
    key="shopify_po", name="Shopify app: B2B PO -> draft-order agent", customer="Shopify merchants selling wholesale",
    model="Marketplace SaaS", price_label={"cons": "$69/mo", "base": "$99/mo", "opt": "$149/mo"},
    channel="Shopify App Store search + outreach to wholesale merchants",
    risk="Small niche with 2+ incumbents; Shopify could ship native PO import",
    kill="<10 installs in the 30 days after listing, or 0 trial-to-paid",
    confidence="L",
    scenarios={
        "cons": Scenario(**_sh, arpu=69, var_cash=40 * PO_PER_ORDER + 2, delivery_h=1.0, churn=0.09, new_sched=[x * 0.4 for x in _sh_sched]),
        "base": Scenario(**_sh, arpu=99, var_cash=60 * PO_PER_ORDER + 2, delivery_h=0.5, churn=0.06, new_sched=_sh_sched),
        "opt": Scenario(**_sh, arpu=149, var_cash=100 * PO_PER_ORDER + 2, delivery_h=0.3, churn=0.04, new_sched=[x * 2 for x in _sh_sched]),
    },
))

# 9. GovCon bid-match + compliance-matrix agent (HigherGov from ~$500/yr, GovDash ~$3k/mo quote [Sourced])
_gc = cold(proc=PROC_SUBSCRIPTION, build_h=[60, 30], maint_h=10, fixed_cash=40, oneoff_cash={1: 100}, onboard_h=1,
           reply_to_call=1.0, call_h=0.5, ramp=[0, 0.3, 0.7] + [1.0] * 9)  # funnel = contacts -> trial -> paid
IDEAS.append(Idea(
    key="govcon", name="GovCon bid-match & proposal-shred agent (SaaS)", customer="Small federal contractors (5-50 staff)",
    model="Vertical SaaS", price_label={"cons": "$99/mo", "base": "$149/mo", "opt": "$249/mo"},
    channel="Cold email to SAM-registered small businesses -> free trial",
    risk="Crowded (HigherGov low end, many AI proposal tools); small firms spend little",
    kill="<15 trial signups from 1,500 contacts, or <2 trial users active weekly",
    confidence="L",
    scenarios={
        "cons": Scenario(**_gc, arpu=99, var_cash=GOVCON_PER_MONTH * 1.5, delivery_h=1.25, churn=0.10, pos_rate=0.003, close_rate=0.12),
        "base": Scenario(**_gc, arpu=149, var_cash=GOVCON_PER_MONTH, delivery_h=0.75, churn=0.07, pos_rate=0.006, close_rate=0.20),
        "opt": Scenario(**_gc, arpu=249, var_cash=GOVCON_PER_MONTH, delivery_h=0.5, churn=0.05, pos_rate=0.010, close_rate=0.30),
    },
))

# 10. Grant prospecting + drafting retainer (writers $2-6k/mo retainers; Instrumentl $299-999/mo [Sourced])
_gr = cold(build_h=[35, 5], maint_h=4, oneoff_cash={1: 100}, onboard_h=4)
IDEAS.append(Idea(
    key="grants", name="Grant prospecting & drafting retainer", customer="Small nonprofits ($250k-$3M budget) without a grant writer",
    model="Productized service, monthly", price_label={"cons": "$400/mo", "base": "$600/mo", "opt": "$900/mo"},
    channel="Cold email to executive directors",
    risk="Thin budgets; grant decisions take 3-9 months so clients churn before proof; funder AI policies",
    kill="<3 positive replies from 1,000 EDs, or 0 paid by day 30",
    confidence="M",
    scenarios={
        "cons": Scenario(**_gr, arpu=400, var_cash=GRANTS_PER_MONTH, delivery_h=7, churn=0.12, pos_rate=0.005, close_rate=0.12),
        "base": Scenario(**_gr, arpu=600, var_cash=GRANTS_PER_MONTH, delivery_h=5, churn=0.08, pos_rate=0.008, close_rate=0.20),
        "opt": Scenario(**_gr, arpu=900, var_cash=GRANTS_PER_MONTH, delivery_h=4, churn=0.05, pos_rate=0.012, close_rate=0.30),
    },
))

# 11. HubSpot marketplace app: CRM hygiene + enrichment agent (no rev share; 3 installs to list [Sourced])
_hs = dict(proc=PROC_SUBSCRIPTION, build_h=[60, 30], maint_h=10, fixed_cash=40, acq_fixed_h=8, onboard_h=0.5, oneoff_cash={1: 50})
_hs_sched = [0, 0, 0, 0.5, 1, 1, 1, 1, 1, 1, 1, 1]  # [Assumption]
IDEAS.append(Idea(
    key="hubspot", name="HubSpot app: CRM hygiene & enrichment agent", customer="SMB HubSpot Starter/Pro portals",
    model="Marketplace SaaS", price_label={"cons": "$49/mo", "base": "$79/mo", "opt": "$129/mo"},
    channel="HubSpot App Marketplace search",
    risk="HubSpot's own Breeze enrichment is free on Starter+ -> commoditized",
    kill="Cannot reach the 3 active installs needed to list within 30 days",
    confidence="M",
    scenarios={
        "cons": Scenario(**_hs, arpu=49, var_cash=1000 * RESEARCH_PER_CONTACT, delivery_h=1.0, churn=0.10, new_sched=[x * 0.4 for x in _hs_sched]),
        "base": Scenario(**_hs, arpu=79, var_cash=1000 * RESEARCH_PER_CONTACT, delivery_h=0.5, churn=0.07, new_sched=_hs_sched),
        "opt": Scenario(**_hs, arpu=129, var_cash=1000 * RESEARCH_PER_CONTACT, delivery_h=0.3, churn=0.05, new_sched=[x * 2.5 for x in _hs_sched]),
    },
))

# 12. Real-estate transaction coordination agent, per file (human TCs $350-450/file [Sourced])
_tc = cold(build_h=[35, 5], maint_h=4, fixed_cash=HOSTING_BASE + TECH_EO_INSURANCE, oneoff_cash={1: 100}, onboard_h=1)
IDEAS.append(Idea(
    key="tc", name="Real-estate transaction-coordination agent", customer="Solo agents and small teams (1-2 states)",
    model="Productized service, per file", price_label={"cons": "$225 x1.5 files", "base": "$250 x2 files", "opt": "$275 x3 files"},
    channel="Cold email + calls to agents",
    risk="Owner hours per file at $101/h erase the margin; E&O exposure on missed deadlines",
    kill="After automation a file still takes >1.5 h of your time, or <2 agents hand you a live file",
    confidence="M",
    scenarios={
        "cons": Scenario(**_tc, arpu=1.5 * 225, var_cash=1.5 * 3, delivery_h=1.5 * 3.5, churn=0.08, pos_rate=0.004, close_rate=0.20),
        "base": Scenario(**_tc, arpu=2 * 250, var_cash=2 * 3, delivery_h=2 * 2.5, churn=0.05, pos_rate=0.006, close_rate=0.30),
        "opt": Scenario(**_tc, arpu=3 * 275, var_cash=3 * 3, delivery_h=3 * 1.8, churn=0.03, pos_rate=0.010, close_rate=0.40),
    },
))


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def simulate(s: Scenario, months: int = MONTHS) -> dict:
    rows = []
    active = 0.0
    history_new = []
    cum_cash = cum_true = cum_hours = 0.0
    build_months = len(s.build_h)
    for m in range(1, months + 1):
        churned = active * s.churn
        retained = active - churned
        build = s.build_h[m - 1] if m <= build_months else 0.0
        maint = s.maint_h if m > build_months or build_months == 0 else 0.0
        acq_on = m >= s.acq_start
        acq_fixed = s.acq_fixed_h if acq_on else 0.0
        calls = s.calls_planned(m)
        new = s.new_customers_planned(m)
        repeat = s.repeat_rate * history_new[m - 3] if (s.mode == "project" and m >= 3) else 0.0

        fixed_hours = build + maint + s.admin_h + acq_fixed + retained * s.delivery_h
        per_call_h = s.call_h + s.pilot_rate * s.pilot_h
        per_new_h = s.onboard_h + s.first_month_factor * s.delivery_h
        want = fixed_hours + calls * per_call_h + (new + repeat) * per_new_h
        capped = False
        if want > HOURS_CAP:
            capped = True
            room = max(0.0, HOURS_CAP - fixed_hours - repeat * per_new_h)
            need = calls * per_call_h + new * per_new_h
            f = min(1.0, room / need) if need > 0 else 0.0
            calls *= f
            new *= f
        history_new.append(new)
        total_new = new + repeat

        billed = retained + total_new * (s.first_month_factor if s.mode == "recurring" else 0.0)
        rec_rev = billed * s.arpu if s.mode == "recurring" else 0.0
        setup_rev = total_new * s.setup_fee
        revenue = rec_rev + setup_rev
        invoices = (retained + total_new) if s.mode == "recurring" else total_new
        processing = revenue * s.proc[0] + invoices * s.proc[1]
        take = revenue * s.take_rate
        variable = billed * s.var_cash + total_new * s.var_cash_oneoff + processing + take
        acq_cash = (s.acq_cash if acq_on else 0.0) + s.acq_oneoff_cash.get(m, 0.0)
        other_fixed = s.fixed_cash + s.oneoff_cash.get(m, 0.0)
        cash_costs = variable + acq_cash + other_fixed

        delivery_hours = retained * s.delivery_h + total_new * per_new_h
        acq_hours = acq_fixed + calls * per_call_h
        hours = build + maint + s.admin_h + acq_hours + delivery_hours

        active = retained + total_new if s.mode == "recurring" else 0.0
        cash_net = revenue - cash_costs
        true_net = cash_net - hours * HOURLY
        cum_cash += cash_net
        cum_true += true_net
        cum_hours += hours
        rows.append(dict(
            m=m, new=total_new, acq_new=new, active=active if s.mode == "recurring" else total_new,
            revenue=revenue, variable=variable, acq_cash=acq_cash, fixed=other_fixed, cash_costs=cash_costs,
            hours=hours, delivery_hours=delivery_hours, acq_hours=acq_hours, cash_net=cash_net, true_net=true_net,
            cum_cash=cum_cash, cum_true=cum_true, cum_hours=cum_hours, capped=capped,
        ))
    return dict(rows=rows)


EXT_MONTHS = 36  # horizon used only to locate break-evens that fall after month 12


def first_month(rows, key) -> int | None:
    """Break-even month: the month after which the cumulative series never dips below 0 again."""
    negative = [r["m"] for r in rows if r[key] < 0]
    if not negative:
        return 1
    return negative[-1] + 1 if negative[-1] < len(rows) else None


def breakeven(s: Scenario, key: str) -> str:
    rows = simulate(s, EXT_MONTHS)["rows"]
    m = first_month(rows, key)
    if m is None:
        return f">{EXT_MONTHS}*"
    return f"{m}*" if m > MONTHS else str(m)


def metrics(idea: Idea, scen: str) -> dict:
    s = idea.scenarios[scen]
    rows = simulate(s)["rows"]
    last = rows[-1]
    revenue = sum(r["revenue"] for r in rows)
    variable = sum(r["variable"] for r in rows)
    acq_cash = sum(r["acq_cash"] for r in rows)
    fixed = sum(r["fixed"] for r in rows)
    hours = sum(r["hours"] for r in rows)
    delivery_h = sum(r["delivery_hours"] for r in rows)
    acq_h = sum(r["acq_hours"] for r in rows)
    acquired = sum(r["acq_new"] for r in rows)
    cash_net = revenue - variable - acq_cash - fixed
    true_profit = cash_net - hours * HOURLY

    # per-customer unit economics (steady state)
    pct, fee = s.proc
    if s.mode == "project":
        price = s.setup_fee
        gp_unit = price * (1 - pct - s.take_rate) - fee - s.var_cash_oneoff - s.onboard_h * HOURLY
        gm = gp_unit / price
        ltv = gp_unit * (1 + s.lifetime_repeats)
        gp_month = gp_unit  # one project ~ one month
        revenue_unit = price
    else:
        revenue_unit = s.arpu
        gp_month = s.arpu * (1 - pct - s.take_rate) - fee - s.var_cash - s.delivery_h * HOURLY
        gm = gp_month / s.arpu
        setup_gp = s.setup_fee * (1 - pct - s.take_rate) - (fee if s.setup_fee else 0) - s.onboard_h * HOURLY
        ltv = gp_month / s.churn + setup_gp
    cash_cac = acq_cash / acquired if acquired else float("inf")
    loaded_cac = (acq_cash + acq_h * HOURLY) / acquired if acquired else float("inf")
    payback = loaded_cac / gp_month if gp_month > 0 else float("inf")

    invest_cash = acq_cash + fixed
    roi_cash = cash_net / invest_cash if invest_cash else float("inf")
    invest_time = invest_cash + (hours - delivery_h) * HOURLY
    roi_time = true_profit / invest_time

    def eff(m):
        r = rows[m - 1]
        return r["cum_cash"] / r["cum_hours"] if r["cum_hours"] else 0.0

    run_rate_12 = last["cash_net"] / last["hours"]
    cross_run = next((r["m"] for r in rows if r["hours"] and r["cash_net"] / r["hours"] >= HOURLY), None)
    cross_cum = next((r["m"] for r in rows if r["cum_hours"] and r["cum_cash"] / r["cum_hours"] >= HOURLY), None)
    # minimum cash actually needed: the deeper of the cumulative-cash trough and month-1 spend
    peak_cash = max(0.0, -min(r["cum_cash"] for r in rows), rows[0]["cash_costs"])
    return dict(
        idea=idea, scen=scen, s=s, rows=rows,
        revenue=revenue, cash_net=cash_net, true_profit=true_profit, hours=hours,
        gp_month=gp_month, gm=gm, revenue_unit=revenue_unit, ltv=ltv,
        cash_cac=cash_cac, loaded_cac=loaded_cac,
        ltv_cac=ltv / loaded_cac if loaded_cac else float("inf"),
        ltv_cash_cac=ltv / cash_cac if cash_cac else float("inf"),
        payback=payback, roi_cash=roi_cash, roi_time=roi_time,
        eff3=eff(3), eff6=eff(6), eff12=eff(12), run12=run_rate_12,
        cross_run=cross_run, cross_cum=cross_cum,
        cash_be=breakeven(s, "cum_cash"),
        true_be=breakeven(s, "cum_true"),
        first_pos=next((r["m"] for r in rows if r["cash_net"] > 0), None),
        peak_cash=peak_cash, avg_hpw=hours / 52, peak_hpw=max(r["hours"] for r in rows) / (52 / 12),
        acquired=acquired, active12=last["active"], mrr12=last["revenue"], capped=any(r["capped"] for r in rows),
    )


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

WEIGHTS = {  # edit to taste; must sum to 1
    "true_profit_base": 0.20,     # 12-mo true profit, base case
    "run12_base": 0.20,           # month-12 effective $/h run-rate
    "true_profit_cons": 0.15,     # downside: conservative-case true profit
    "roi_time_base": 0.10,
    "ltv_cac_base": 0.10,
    "true_be_base": 0.10,         # earlier is better
    "confidence": 0.15,
}


def be_num(x: str) -> float:
    if x.startswith(">"):
        return 99.0
    return float(x.strip("~*"))


def overall(results: dict) -> list:
    keys = list(results)
    feats = {
        "true_profit_base": {k: results[k]["base"]["true_profit"] for k in keys},
        "run12_base": {k: results[k]["base"]["run12"] for k in keys},
        "true_profit_cons": {k: results[k]["cons"]["true_profit"] for k in keys},
        "roi_time_base": {k: results[k]["base"]["roi_time"] for k in keys},
        "ltv_cac_base": {k: results[k]["base"]["ltv_cac"] for k in keys},
        "true_be_base": {k: -be_num(results[k]["base"]["true_be"]) for k in keys},
        "confidence": {k: {"H": 1.0, "M": 0.5, "L": 0.0}[results[k]["base"]["idea"].confidence] for k in keys},
    }
    scores = {k: 0.0 for k in keys}
    n = len(keys)
    for f, w in WEIGHTS.items():
        if f == "confidence":
            for k in keys:
                scores[k] += w * 100 * feats[f][k]
            continue
        ordered = sorted(keys, key=lambda k: feats[f][k])
        for i, k in enumerate(ordered):  # percentile rank 0..100 (ties broken by order)
            scores[k] += w * 100 * i / (n - 1)
    return sorted(keys, key=lambda k: -scores[k]), scores


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def money(x: float) -> str:
    if x == float("inf"):
        return "n/a"
    sign = "-" if x < 0 else ""
    x = abs(x)
    if x >= 10_000:
        return f"{sign}${x/1000:.0f}k"
    if x >= 1000:
        return f"{sign}${x/1000:.1f}k"
    return f"{sign}${x:.0f}"


def ratio(x: float) -> str:
    if x == float("inf"):
        return "n/a"
    return f"{x:.1f}"


def months(x: float) -> str:
    if x == float("inf"):
        return "never"
    return f"{x:.1f}"


def pct(x: float) -> str:
    return f"{x*100:.0f}%"


def report(results: dict) -> str:
    out = []
    w = out.append
    w("# Model output (generated by analysis/model.py)\n")
    w(f"Owner time valued at ${HOURLY:.0f}/h; hours cap {HOURS_CAP:.1f} h/month (20 h/wk). "
      "`*` = break-even falls after month 12; found by running the same plan (same outreach, capacity cap) for 36 months. "
      "Customer counts are expected values.\n")
    w("## Unit token costs used\n")
    w(f"- Security questionnaire (250 Qs): ${SECQ_PER_QUESTIONNAIRE:.2f}")
    w(f"- Medical chronology: ${MEDCHRON_PER_PAGE:.4f}/page")
    w(f"- Account research: ${RESEARCH_PER_CONTACT:.4f}/contact (+${EMAIL_FIND_VERIFY_PER_CONTACT:.2f} find/verify)")
    w(f"- Freight email: ${FREIGHT_PER_EMAIL:.4f}; Shopify PO: ${PO_PER_ORDER:.4f}; GovCon: ${GOVCON_PER_MONTH:.2f}/mo; Grants: ${GRANTS_PER_MONTH:.2f}/mo")
    w(f"- Cold-email stack: ${COLD_EMAIL_STACK:.0f}/mo + ${DOMAINS_ONEOFF} domains once\n")

    for scen in ("base", "cons", "opt"):
        w(f"## Comparison table: {scen}\n")
        w("| Idea | Price | GM % | Cash CAC | Loaded CAC | LTV | LTV:CAC (loaded / cash) | Payback (mo) | Min. cash needed | Avg / peak h/wk | Cash BE (mo) | True BE (mo) | 12-mo cash net | 12-mo true profit | ROI cash / time-adj | $/h m3 / m6 / m12 (cum) | $/h m12 run-rate | Active m12 | Capacity-capped |")
        w("|" + "---|" * 19)
        for k, r in results.items():
            m = r[scen]
            i = m["idea"]
            w(f"| {i.name} | {i.price_label[scen]} | {pct(m['gm'])} | {money(m['cash_cac'])} | {money(m['loaded_cac'])} | {money(m['ltv'])} | "
              f"{ratio(m['ltv_cac'])} / {ratio(m['ltv_cash_cac'])} | {months(m['payback'])} | {money(m['peak_cash'])} | "
              f"{m['avg_hpw']:.1f} / {m['peak_hpw']:.1f} | {m['cash_be']} | {m['true_be']} | {money(m['cash_net'])} | {money(m['true_profit'])} | "
              f"{pct(m['roi_cash'])} / {pct(m['roi_time'])} | {money(m['eff3'])} / {money(m['eff6'])} / {money(m['eff12'])} | {money(m['run12'])} | "
              f"{m['active12']:.1f} | {'yes' if m['capped'] else 'no'} |")
        w("")

    w("## Scenario spread (12-month cash net / true profit / month-12 $/h run-rate)\n")
    w("| Idea | Conservative | Base | Optimistic |")
    w("|---|---|---|---|")
    for k, r in results.items():
        cells = [f"{money(r[sc]['cash_net'])} / {money(r[sc]['true_profit'])} / {money(r[sc]['run12'])}" for sc in ("cons", "base", "opt")]
        w(f"| {r['base']['idea'].name} | " + " | ".join(cells) + " |")
    w("")

    base = {k: r["base"] for k, r in results.items()}
    lists = {
        "(a) Fastest positive cash flow (cash break-even, then 12-mo cash net)":
            sorted(base, key=lambda k: (be_num(base[k]["cash_be"]), -base[k]["cash_net"])),
        "(b) Best 12-month time-adjusted ROI": sorted(base, key=lambda k: -base[k]["roi_time"]),
        "(c) Best LTV:CAC (loaded)": sorted(base, key=lambda k: -base[k]["ltv_cac"]),
        "(d) Fastest true break-even": sorted(base, key=lambda k: (be_num(base[k]["true_be"]), -base[k]["true_profit"])),
        "(e) Highest effective $/h by month 12 (cumulative)": sorted(base, key=lambda k: -base[k]["eff12"]),
    }
    w("## Category rankings (base case, top 5)\n")
    for title, order_ in lists.items():
        w(f"**{title}**: " + "; ".join(f"{n}. {base[k]['idea'].name}" for n, k in enumerate(order_[:5], 1)) + "\n")

    order, scores = overall(results)
    w("## Overall ranking (base case)\n")
    w("Weights: " + ", ".join(f"{k} {v:.0%}" for k, v in WEIGHTS.items()) + "\n")
    w("| # | Idea | Score |")
    w("|---|---|---|")
    for n, k in enumerate(order, 1):
        w(f"| {n} | {results[k]['base']['idea'].name} | {scores[k]:.0f} |")
    w("")

    w("## Month-by-month, base case (all ideas)\n")
    for k in order:
        m = results[k]["base"]
        w(f"### {m['idea'].name}\n")
        w("| Month | New | Active | Revenue | Cash costs | Hours | Cash net | Cum. cash | Cum. true profit |")
        w("|---|---|---|---|---|---|---|---|---|")
        for r in m["rows"]:
            w(f"| {r['m']} | {r['new']:.2f} | {r['active']:.1f} | {money(r['revenue'])} | {money(r['cash_costs'])} | {r['hours']:.0f}{' (cap)' if r['capped'] else ''} | "
              f"{money(r['cash_net'])} | {money(r['cum_cash'])} | {money(r['cum_true'])} |")
        w("")
    return "\n".join(out)


def run_all() -> dict:
    return {i.key: {sc: metrics(i, sc) for sc in ("cons", "base", "opt")} for i in IDEAS}


def sensitivity() -> str:
    """Re-rank under single-assumption shocks to show what moves the order."""
    shocks = {
        "close rates x0.5": lambda s: replace(s, close_rate=s.close_rate * 0.5, new_sched=[x * 0.5 for x in s.new_sched] if s.new_sched else None),
        "delivery hours x1.5": lambda s: replace(s, delivery_h=s.delivery_h * 1.5, onboard_h=s.onboard_h * 1.5),
        "churn x1.5": lambda s: replace(s, churn=min(1.0, s.churn * 1.5)),
        "prices x0.8": lambda s: replace(s, arpu=s.arpu * 0.8, setup_fee=s.setup_fee * 0.8),
        "variable cash costs x3": lambda s: replace(s, var_cash=s.var_cash * 3, var_cash_oneoff=s.var_cash_oneoff * 3),
        "owner time valued at $50/h": "hourly50",
    }
    base_order, _ = overall(run_all())
    lines = ["## Sensitivity: overall top 5 under single shocks\n",
             "| Shock | #1 | #2 | #3 | #4 | #5 |", "|---|---|---|---|---|---|",
             "| (none) | " + " | ".join(base_order[:5]) + " |"]
    global IDEAS, HOURLY
    saved, saved_hourly = IDEAS, HOURLY
    for label, fn in shocks.items():
        if fn == "hourly50":
            HOURLY = 50.0
        else:
            IDEAS = [replace(i, scenarios={k: fn(v) for k, v in i.scenarios.items()}) for i in saved]
        order, _ = overall(run_all())
        lines.append(f"| {label} | " + " | ".join(order[:5]) + " |")
        IDEAS, HOURLY = saved, saved_hourly
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    res = run_all()
    text = report(res) + "\n" + sensitivity()
    if "--stdout" in sys.argv:
        print(text)
    else:
        path = Path(__file__).with_name("results.md")
        path.write_text(text)
        print(f"wrote {path}")
