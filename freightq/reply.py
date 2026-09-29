"""Draft replies, filled in by code from the checked data: the model never writes a price or a date into them."""

from __future__ import annotations

from datetime import date

from .broker import BLOCKING, EQUIPMENT_LABELS, FIELD_LABELS, BrokerConfig
from .extract import EmailResult, Shipment

PRICE_BY_HAND = "[[PRICE BY HAND]]"
EXTRA_LABELS = {"extra_stop": "stop-off", "tarps": "tarps", "hazmat": "hazmat", "team": "team drivers",
                "liftgate": "liftgate"}


def money(x: float) -> str:
    return f"${x:,.0f}" if float(x).is_integer() else f"${x:,.2f}"


def day_text(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%a %m/%d/%Y") if iso else ""


def join_words(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _details(s: Shipment) -> list[str]:
    what = [EQUIPMENT_LABELS.get(s.equipment, "")]
    if s.weight_lbs is not None:
        what.append(f"{s.weight_lbs:,.0f} lbs")
    goods = s.commodity + (f" ({s.pieces})" if s.pieces and s.commodity else s.pieces)
    what += [goods, f"temp {s.temperature}" if s.temperature else "", "hazmat" if s.hazmat else "",
             *s.requirements]
    lines = [", ".join(dict.fromkeys(w for w in what if w))]
    when = []
    if s.pickup_date:
        when.append(f"pickup {day_text(s.pickup_date)} {s.pickup_time}".strip())
    if s.delivery_date:
        when.append(f"deliver {day_text(s.delivery_date)} {s.delivery_time}".strip())
    if when:
        text = "; ".join(when)
        lines.append(text[0].upper() + text[1:])
    if s.references:
        lines.append("Refs: " + ", ".join(s.references))
    return [x for x in lines if x]


def _rate_lines(s: Shipment) -> list[str]:
    q = s.quote
    blocked = [f for f in s.missing if f in BLOCKING]
    if s.kind == "tender" and s.rate is not None:
        return [f"Confirming we'll cover this load at {money(s.rate)} all-in."]
    if blocked:
        return [f"To quote this lane we need the {join_words([FIELD_LABELS[f] for f in s.missing])}."]
    if q is None or not q.priced:
        return [f"Rate: {PRICE_BY_HAND}"]
    rate = f"Rate: {money(q.total)} all-in"
    if q.extras:
        rate += f" ({money(q.linehaul)} linehaul + " + " + ".join(
            f"{money(v)} {EXTRA_LABELS.get(k, k)}" for k, v in q.extras) + ")"
    if s.truckloads > 1:
        rate += f" per truck, {s.truckloads} trucks"
    return [rate]


def draft_reply(r: EmailResult, cfg: BrokerConfig) -> str | None:
    """The reply to one email, or None when there's nothing to answer (not a request, or the model failed)."""
    if r.kind == "other" or r.error or not r.shipments:
        return None
    e = r.email
    name = (r.contact or e.sender_name).split()
    subject = e.subject if e.subject.lower().startswith("re:") else f"Re: {e.subject}"
    tender = r.kind == "tender"
    priced = any(s.quote is not None and s.quote.priced for s in r.shipments)
    lines = [f"To: {e.sender}", f"Subject: {subject}", "", f"Hi {name[0] if name else 'there'},", ""]
    if tender:
        lines.append("Thanks for the tender.")
    else:
        lines.append("Thanks for the request. Here's our pricing:" if priced else "Thanks for the request.")
    lines.append("")
    for i, s in enumerate(r.shipments, 1):
        lines.append((f"{i}. " if len(r.shipments) > 1 else "") + s.lane_text())
        lines += _details(s) + _rate_lines(s)
        ask = [FIELD_LABELS[f] for f in s.missing if f not in BLOCKING]
        if ask and not [f for f in s.missing if f in BLOCKING]:
            lines.append(f"Please confirm the {join_words(ask)}.")
        lines.append("")
    if priced and not tender:
        lines += [f"Rates are good for {cfg.quote_valid_days} days, subject to truck availability. "
                  "Reply to book and we'll send the rate confirmation.", ""]
    elif tender:
        lines += ["Rate confirmation and truck details to follow.", ""]
    lines += ["Thanks,", cfg.signature.strip() or cfg.name]
    return "\n".join(lines) + "\n"
