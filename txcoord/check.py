"""Checks a contract reading in code and works out every deadline: anchors, wording, addenda, price and order."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from agentkit.checks import emails_in, find_dates, has_amount, parse_date, quote_found

from .config import TCConfig
from .dates import Calendar, Rule, compute, parse_term, time_rules, words_to_number
from .extract import ContractReading

AFTER_CLOSING_OK = re.compile(r"walk|possession|closing|settlement|move", re.I)


@dataclass
class Flag:
    code: str
    hold: bool  # True: fix before the timeline goes out
    text: str


@dataclass
class Deadline:
    name: str
    term: str = ""
    quote: str = ""
    party: str = ""
    due: date | None = None
    how: str = ""
    status: str = ""  # "past", "soon", "upcoming" or "no date"
    flags: list[Flag] = field(default_factory=list)


@dataclass
class Transaction:
    folder: str
    documents: list[str] = field(default_factory=list)
    address: str = ""
    buyers: list[str] = field(default_factory=list)
    sellers: list[str] = field(default_factory=list)
    price: float | None = None
    earnest_money: float | None = None
    financing: str = ""
    acceptance: date | None = None
    closing: date | None = None
    days_mean: str = ""
    roll: bool = True
    deadlines: list[Deadline] = field(default_factory=list)
    contacts: list[dict] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    error: str = ""

    def all_flags(self) -> list[Flag]:
        return self.flags + [f for d in self.deadlines for f in d.flags]

    @property
    def ready(self) -> bool:
        return not self.error and self.closing is not None and not any(f.hold for f in self.all_flags())


def _date_in(d: date | None, text: str, ref: date | None) -> bool:
    return d is not None and d in find_dates(text, ref)


def _status(d: date | None, today: date, soon: int) -> str:
    if d is None:
        return "no date"
    if d < today:
        return "past"
    return "soon" if (d - today).days <= soon else "upcoming"


def check_contract(r: ContractReading, folder: str, docs: list[tuple[str, str]], cfg: TCConfig, today: date
                   ) -> Transaction:
    text = "\n".join(t for _, t in docs)
    by_doc = dict(docs)
    t = Transaction(folder=folder, documents=[n for n, _ in docs], address=r.property, buyers=r.buyers,
                    sellers=r.sellers, financing=r.financing)
    flags = t.flags

    # the anchors every deadline hangs on
    acc = parse_date(r.acceptance_date)
    if acc is None or not _date_in(acc, text, None):
        flags.append(Flag("acceptance_not_in_text", True, f"acceptance date '{r.acceptance_date or 'none'}' is not in "
                                                          "the contract: every deadline counts from it"))
        acc = None
    t.acceptance = acc
    closing = parse_date(r.closing_date)
    if closing is None or not _date_in(closing, text, acc):
        flags.append(Flag("closing_not_in_text", True, f"closing date '{r.closing_date or 'none'}' is not in the "
                                                       "contract"))
        closing = None
    for ch in r.changes:
        if ch.quote and not quote_found(ch.quote, by_doc.get(ch.document, text), 0.8):
            flags.append(Flag("change_not_in_text", True, f"the change '{ch.quote[:70]}' is not in {ch.document}"))
            continue
        t.changes.append({"document": ch.document, "term": ch.term, "new_value": ch.new_value, "quote": ch.quote})
    changed = [c for c in t.changes if re.search(r"clos|settle", c["term"], re.I)]
    if changed and closing:
        latest = changed[-1]
        new = parse_date(latest["new_value"]) or next(iter(find_dates(latest["quote"])), None)
        if new and new != closing:
            flags.append(Flag("closing_change_not_applied", True,
                              f"{latest['document']} moves closing to {new.isoformat()}, but the reading says "
                              f"{closing.isoformat()}; using {new.isoformat()}"))
            closing = new
    # an addendum that moves closing wins, even when the reading missed it
    for name, body in docs[1:]:
        for sentence in re.split(r"(?<=[.;])\s+", body):
            ds = find_dates(sentence, acc) if re.search(r"\bclos|\bsettle", sentence, re.I) else set()
            if len(ds) == 1 and closing and next(iter(ds)) != closing:
                new = next(iter(ds))
                flags.append(Flag("closing_change_not_applied", True, f"{name} says '{sentence.strip()[:90]}', but the "
                                  f"reading says closing is {closing.isoformat()}; using {new.isoformat()}"))
                closing = new
    t.closing = closing

    # money
    if r.price is not None:
        if not has_amount(r.price, text):
            flags.append(Flag("price_not_in_text", True, f"price ${r.price:,.0f} is not in the contract"))
        t.price = r.price
        if r.price_in_words:
            spelled = words_to_number(r.price_in_words)
            if not quote_found(r.price_in_words, text, 0.9):
                flags.append(Flag("price_words_not_in_text", False, f"price in words '{r.price_in_words}' is not in "
                                                                     "the contract"))
            elif spelled is not None and abs(spelled - r.price) > 0.5:
                flags.append(Flag("price_words", True, f"the price in words is ${spelled:,.0f} but in figures "
                                                       f"${r.price:,.0f}: get it corrected and initialed"))
    else:
        flags.append(Flag("no_price", True, "no purchase price found"))
    if r.earnest_money is not None:
        if not has_amount(r.earnest_money, text):
            flags.append(Flag("earnest_not_in_text", True, f"earnest money ${r.earnest_money:,.0f} is not in the "
                                                           "contract"))
        elif t.price:
            share = r.earnest_money / t.price
            if share > 1:
                flags.append(Flag("earnest_over_price", True, "earnest money is more than the price"))
            elif share > 0.10 or share < 0.005:
                flags.append(Flag("earnest_unusual", False, f"earnest money is {share:.1%} of the price; confirm "
                                                            "the figure"))
        t.earnest_money = r.earnest_money
    if r.loan_amount and r.down_payment and t.price and abs(r.loan_amount + r.down_payment - t.price) > 1:
        flags.append(Flag("loan_math", False, f"loan ${r.loan_amount:,.0f} + down payment ${r.down_payment:,.0f} is "
                                              f"not the price ${t.price:,.0f}"))

    # how days count: the contract's words win over the coordinator's defaults
    days_mean, roll = time_rules(text)
    if not days_mean or roll is None:
        flags.append(Flag("time_rules_missing", False, "the contract doesn't say "
                          + " or ".join(x for x, missing in (("what 'days' means", not days_mean),
                                                             ("how weekend deadlines move", roll is None)) if missing)
                          + f"; using {cfg.days_mean} days" + (", moved to the next business day" if
                                                              cfg.roll_to_business_day else "")))
    t.days_mean = days_mean or cfg.days_mean
    t.roll = cfg.roll_to_business_day if roll is None else roll

    cal = Calendar(cfg.federal_holidays, cfg.extra_holidays)
    anchors = {"acceptance": acc, "closing": closing}
    for x in r.deadlines:
        d = Deadline(name=x.name or "deadline", term=x.term, quote=x.quote, party=x.party.lower())
        if not x.term or not (quote_found(x.term, text, 0.85) or (x.quote and quote_found(x.quote, text, 0.85)
                                                                  and quote_found(x.term, x.quote, 0.85))):
            d.flags.append(Flag("deadline_not_in_text", True, f"the wording '{x.term or x.quote[:60]}' is not in the "
                                                              "contract"))
        else:
            rule: Rule = parse_term(x.term, t.days_mean)
            if rule.kind == "unknown" and x.quote:
                rule = parse_term(x.quote, t.days_mean)
            d.due, d.how = compute(rule, anchors, cal, t.roll)
            if rule.kind == "event":
                d.flags.append(Flag("depends_on_event", False, d.how))
            elif d.due is None:
                d.flags.append(Flag("deadline_unknown", True, f"{d.how}: '{x.term}'"))
        if d.due and closing and d.due > closing and not AFTER_CLOSING_OK.search(d.name):
            d.flags.append(Flag("after_closing", True, f"{d.name} ends {d.due.isoformat()}, after closing on "
                                                       f"{closing.isoformat()}: the parties must fix the dates"))
        if d.due and acc and d.due < acc:
            d.flags.append(Flag("before_acceptance", True, f"{d.name} ends {d.due.isoformat()}, before acceptance"))
        d.status = _status(d.due, today, cfg.soon_days)
        t.deadlines.append(d)
    if closing:
        if not cal.business_day(closing):
            flags.append(Flag("closing_not_business_day", False, f"closing {closing.isoformat()} is a "
                                                                 f"{cal.why_not(closing)}"))
        t.deadlines.append(Deadline("Closing", r.closing_date, "", "both", closing,
                                    "closing date" + (f" ({t.changes[-1]['document']})" if changed else ""),
                                    _status(closing, today, cfg.soon_days)))
    t.deadlines.sort(key=lambda d: (d.due is None, d.due or date.max))

    # contacts: emails must be in the documents
    found = set(emails_in(text))
    for c in r.contacts:
        email = c.email.lower()
        if email and email not in found:
            flags.append(Flag("contact_email_not_in_text", False, f"{c.name or c.role}'s email {c.email} is not in the "
                                                                  "documents; left out"))
            email = ""
        t.contacts.append({"role": c.role, "name": c.name, "company": c.company, "email": email})
    return t
