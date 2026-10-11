"""Sorts replies to the campaign, drafts the next response, and decides opt-outs, bounces and dates in code."""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta

from pydantic import BaseModel, Field, field_validator

from agentkit.checks import MONTHS, emails_in, find_dates, quote_found, relative_dates, words
from agentkit.inbox import MAX_PART, Email

from .campaign import Campaign
from .leads import Lead
from .sequence import Flag, check_text

CATEGORIES = ["interested", "question", "objection", "not_now", "referral", "wrong_person", "not_interested",
              "unsubscribe", "out_of_office", "bounce", "other"]
NO_REPLY = {"not_interested", "unsubscribe", "out_of_office", "bounce", "other"}  # never answer these
SUPPRESS = {"not_interested": "declined", "unsubscribe": "asked not to be contacted", "bounce": "bounced"}
PRIORITY = {"interested": 1, "question": 2, "referral": 3, "objection": 4, "wrong_person": 4, "not_now": 5,
            "out_of_office": 6, "other": 6, "not_interested": 7, "unsubscribe": 7, "bounce": 8}

SYSTEM = """You sort replies to cold emails sent for {client} and draft the next response.

Categories:
- interested: wants to talk, asks for times, says yes.
- question: asks something before deciding (price, details, fit).
- objection: pushes back but hasn't said stop ("we already have a provider", "no budget").
- not_now: asks to be contacted later. Put the date they gave in follow_up_date (YYYY-MM-DD), else "".
- referral: names someone else to talk to. Copy their name and email exactly as written.
- wrong_person: not the right person and names nobody.
- not_interested: a clear no.
- unsubscribe: asks to be removed or not emailed again.
- out_of_office: an automatic away message. Put the return date in follow_up_date.
- bounce: a delivery failure.
- other: anything else.

Rules:
- quote: the sentence from the newest message that shows the category, copied word for word.
- Draft "reply" only for interested, question, objection, not_now, referral and wrong_person; otherwise "". Keep it under {max_words} words, warm and plain, in the sender's voice, without a signature.
- For interested, offer the booking link. For a question, answer only from OFFER and PROOF; if they don't answer it, say the sender will find out, and set needs_client to true with the reason.
- Never invent prices, numbers, clients or promises. Cite proof you use by id in proof_used.
- Reply with one JSON object only."""

SCHEMA_HINT = """{"category": "<one category>", "quote": "<sentence from the newest message, word for word>", "summary": "<one line>", "follow_up_date": "<YYYY-MM-DD or ''>", "referral_name": "<name or ''>", "referral_email": "<email or ''>", "questions": ["<each question they asked>"], "needs_client": <true|false>, "needs_client_reason": "<what the client must decide, or ''>", "reply": "<draft reply or ''>", "proof_used": ["P1"]}"""


def system_prompt(c: Campaign) -> str:
    return SYSTEM.format(client=c.client, max_words=c.reply_max_words)


def _clip(text: str) -> str:
    return text if len(text) <= MAX_PART else text[:MAX_PART] + "\n[... cut ...]"


def build_prompt(e: Email, lead: Lead | None, c: Campaign) -> str:
    sent = e.sent.strftime("%A %Y-%m-%d") if e.sent else "unknown"
    head = [f"Reply file: {e.file}", f"Sent: {sent}", f"From: {e.sender_name} <{e.sender}>".replace(" <>", ""),
            f"Subject: {e.subject}"]
    blocks = ["\n".join(head), "### Newest message\n" + _clip(e.latest or "(empty)")]
    if e.earlier:
        blocks.append("### Earlier in the thread (our emails, for context)\n" + _clip(e.earlier))
    if lead:
        blocks.append("### Who they are\n" + "\n".join(f"- {k}: {v}" for k, v in lead.facts()))
    blocks.append(f"### Our side\nCLIENT: {c.client}\nSENDER: {c.sender_name}, {c.sender_title}\nOFFER: {c.offer}\n"
                  f"PROOF:\n{c.proof_block() or '(none)'}\nBOOKING LINK: {c.booking_link or '(none)'}")
    return "\n\n".join(blocks) + f"\n\nReturn JSON in exactly this shape (replace every <...>):\n{SCHEMA_HINT}"


def _str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(str(x) for x in v if x not in (None, ""))
    return str(v).strip()


class Triage(BaseModel):
    category: str = "other"
    quote: str = ""
    summary: str = ""
    follow_up_date: str = ""
    referral_name: str = ""
    referral_email: str = ""
    questions: list[str] = Field(default_factory=list)
    needs_client: bool = False
    needs_client_reason: str = ""
    reply: str = ""
    proof_used: list[str] = Field(default_factory=list)

    @field_validator("quote", "summary", "follow_up_date", "referral_name", "referral_email", "needs_client_reason",
                     "reply", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("category", mode="before")
    @classmethod
    def _cat(cls, v):
        v = re.sub(r"[\s-]+", "_", _str(v).lower())
        aliases = {"ooo": "out_of_office", "out_of_the_office": "out_of_office", "auto_reply": "out_of_office",
                   "positive": "interested", "meeting": "interested", "later": "not_now", "remove": "unsubscribe",
                   "opt_out": "unsubscribe", "negative": "not_interested", "decline": "not_interested"}
        v = aliases.get(v, v)
        return v if v in CATEGORIES else "other"

    @field_validator("questions", "proof_used", mode="before")
    @classmethod
    def _l(cls, v):
        if v is None or v == "":
            return []
        return [_str(x) for x in (v if isinstance(v, list) else [v]) if _str(x)]

    @field_validator("needs_client", mode="before")
    @classmethod
    def _b(cls, v):
        return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1")


# --- rules decided in code ----------------------------------------------------------------------------------------

BOUNCE_SENDER = re.compile(r"mailer-daemon|postmaster|mail delivery (?:sub)?system|microsoftexchange", re.I)
BOUNCE_SUBJECT = re.compile(r"undeliverable|delivery status notification|mail delivery failed|returned mail|"
                            r"delivery failure|failure notice|could not be delivered|address not found", re.I)
OOO_SUBJECT = re.compile(r"out of (?:the )?office|automatic reply|auto[- ]?reply|autoreply|away from (?:the )?office",
                         re.I)
OOO_BODY = re.compile(r"\b(?:i am|i'm|i will be)\s+(?:currently\s+)?(?:out of (?:the )?office|away from (?:the )?office|"
                      r"on (?:vacation|leave|holiday|pto|parental leave))|\blimited access to (?:my )?e-?mail", re.I)
OPT_OUT = re.compile(r"\b(?:unsubscribe me|please unsubscribe|remove me|take me off|stop (?:e-?mailing|contacting|"
                     r"sending|messaging|reaching out)|do not (?:e-?mail|contact|message)|don'?t (?:e-?mail|contact|"
                     r"message|reach out)|opt (?:me )?out|no more e-?mails|never contact)\b", re.I)
SHORT_STOP = re.compile(r"^\W*(?:unsubscribe|stop|remove|no thanks)\W*(?:please|thanks|thank you)?\W*$", re.I)


def code_category(e: Email) -> str:
    """The categories decided by rules, not the model: delivery failures, opt-outs and away messages."""
    if BOUNCE_SENDER.search(f"{e.sender} {e.sender_name}") or BOUNCE_SUBJECT.search(e.subject):
        return "bounce"
    latest = e.latest or ""
    if OPT_OUT.search(latest) or SHORT_STOP.match(latest):
        return "unsubscribe"
    if OOO_SUBJECT.search(e.subject) or OOO_BODY.search(latest):
        return "out_of_office"
    return ""


def first_weekday(y: int, m: int) -> date:
    d = date(y, m, 1)
    return d + timedelta(days=max(0, 7 - d.weekday()) if d.weekday() >= 5 else 0)


def add_months(d: date, n: int) -> date:
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


MONTH_WORD = (r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|"
              r"jun|jul|aug|sept?|oct|nov|dec)")
COUNT = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "six": 6, "a couple of": 2, "a couple": 2, "a few": 3,
         "couple of": 2, "few": 3}


def later_dates(text: str, sent: date) -> set[date]:
    """Every date a 'contact me later' message can mean, worked out from the day it was sent."""
    low = (text or "").lower()
    out = {d for d in find_dates(text, ref=sent) | relative_dates(text, sent) if d > sent}
    for m in re.finditer(rf"\b(?:in|after|until|around|early|mid|late|by)\s+(?:early\s+|mid-?|late\s+)?{MONTH_WORD}\b"
                         r"(?!\s*\d)", low):
        mo = MONTHS[m.group(1)[:4] if m.group(1).startswith("sept") else m.group(1)[:3]]
        y = sent.year + (1 if mo <= sent.month else 0)
        out.add(first_weekday(y, mo))
    if re.search(r"\bnext quarter\b", low):
        q = (sent.month - 1) // 3 + 1
        out.add(first_weekday(sent.year + (q == 4), (q % 4) * 3 + 1))
    for m in re.finditer(r"\bq([1-4])(?:\s*(?:of\s*)?(20\d\d))?\b", low):
        mo = (int(m.group(1)) - 1) * 3 + 1
        y = int(m.group(2)) if m.group(2) else sent.year + (1 if mo <= sent.month else 0)
        out.add(first_weekday(y, mo))
    if re.search(r"\b(?:new year|after the holidays|next year|in the new year)\b", low):
        out.add(first_weekday(sent.year + 1, 1))
    if re.search(r"\bnext month\b", low):
        nm = add_months(sent, 1)
        out.add(first_weekday(nm.year, nm.month))
    for m in re.finditer(r"\bin\s+(a couple of|a couple|a few|couple of|few|a|one|two|three|four|six|\d+)\s+"
                         r"(week|month)s?\b", low):
        n = COUNT.get(m.group(1)) or int(m.group(1))
        out.add(sent + timedelta(weeks=n) if m.group(2) == "week" else add_months(sent, n))
    return out


# --- checking one triaged reply -----------------------------------------------------------------------------------

@dataclass
class ReplyResult:
    email: Email
    lead: Lead | None = None
    category: str = ""
    model_category: str = ""
    summary: str = ""
    quote: str = ""
    follow_up: str = ""
    referral_name: str = ""
    referral_email: str = ""
    other_contacts: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    needs_client: str = ""  # what the client must decide, if anything
    reply: str = ""
    flags: list[Flag] = field(default_factory=list)
    error: str = ""
    suppress_email: str = ""
    suppress_reason: str = ""
    draft_file: str = ""

    @property
    def priority(self) -> int:
        return PRIORITY.get(self.category, 6) if not self.error else 9


def ours(addr: str, c: Campaign) -> bool:
    return addr.split("@")[-1].lower() in c.sending_domains


def bounced_address(e: Email, c: Campaign) -> str:
    """The failed recipient in a bounce message: the first address that isn't ours or the mail server's."""
    for addr in emails_in(e.all_text()):
        if addr != e.sender.lower() and not ours(addr, c) and not BOUNCE_SENDER.search(addr):
            return addr
    return ""


def rule_result(e: Email, lead: Lead | None, c: Campaign, category: str) -> ReplyResult:
    """Bounces, opt-outs and away messages, handled in code with no model request: no reply is ever drafted."""
    quote = ""
    m = OPT_OUT.search(e.latest or "") if category == "unsubscribe" else None
    if m:
        start = max((e.latest or "").rfind(x, 0, m.start()) for x in (".", "!", "?", "\n")) + 1
        quote = (e.latest or "")[start:].split("\n")[0].strip()
    r = check_triage(Triage(category=category, quote=quote), e, lead, c)
    if category == "bounce":
        r.summary = f"bounced: {r.suppress_email or 'address not found in the message'}"
    elif category == "unsubscribe":
        r.summary = "asked not to be emailed again"
    else:
        r.summary = f"away until {r.follow_up}" if r.follow_up else "away message"
    return r


def check_triage(t: Triage, e: Email, lead: Lead | None, c: Campaign) -> ReplyResult:
    r = ReplyResult(email=e, lead=lead, model_category=t.category, summary=t.summary, questions=t.questions)
    flags = r.flags
    latest = e.latest or ""
    sent = e.day or date.today()
    forced = code_category(e)
    r.category = forced or t.category
    if forced and t.category != forced:
        flags.append(Flag(0, False, f"the model said '{t.category}'; the email is '{forced}' (decided by rule)"))
    if not forced and t.category in ("bounce", "out_of_office"):
        flags.append(Flag(0, False, f"the model said '{t.category}' but the email doesn't look automatic; check it"))
    if t.quote and not quote_found(t.quote, e.all_text(), 0.8):
        flags.append(Flag(0, False, f"the model's quote is not in the email: '{t.quote[:80]}'"))
    else:
        r.quote = t.quote
    if lead is None and r.category != "bounce":
        flags.append(Flag(0, False, f"{e.sender or 'the sender'} is not in the lead list (a forward or a new address?)"))

    # who to stop emailing
    if r.category == "bounce":
        r.suppress_email = bounced_address(e, c)
        if not r.suppress_email:
            flags.append(Flag(0, True, "a bounce, but the failed address isn't in it; find it in the sequencer"))
    elif r.category in SUPPRESS:
        r.suppress_email = e.sender.lower()
    r.suppress_reason = SUPPRESS.get(r.category, "") if r.suppress_email else ""

    # when to follow up
    if r.category in ("not_now", "out_of_office"):
        cands = later_dates(latest, sent)
        if r.category == "out_of_office":
            cands = {d for d in cands if (d - sent).days <= 120}
            pick = max(cands) if cands else None
            if pick is None:
                pick = sent + timedelta(days=7)
                flags.append(Flag(0, False, "no return date in the away message; follow up in a week"))
        else:
            model = t.follow_up_date[:10]
            pick = next((d for d in cands if d.isoformat() == model), None)
            if pick is None and len(cands) == 1:
                pick = next(iter(cands))
                if model:
                    flags.append(Flag(0, False, f"the model said {model}; the email means {pick.isoformat()}"))
            elif pick is None and cands:
                pick = min(cands)
                flags.append(Flag(0, False, "the email could mean " + ", ".join(sorted(d.isoformat() for d in cands))
                                  + f"; using {pick.isoformat()}"))
            elif pick is None:
                pick = add_months(sent, 3)
                flags.append(Flag(0, False, f"no date given; follow up in 3 months ({pick.isoformat()})"))
        r.follow_up = pick.isoformat()

    # referrals and other people named
    named = [a for a in emails_in(latest) if a != e.sender.lower() and not ours(a, c)]
    if r.category == "referral":
        ref = t.referral_email.strip().lower()
        if ref and ref not in named:
            flags.append(Flag(0, True, f"referral email {ref} is not in the reply"))
            ref = ""
        if not ref and len(named) == 1:
            ref = named[0]
        r.referral_email = ref
        name = t.referral_name.strip()
        if name and not all(w in words(latest) for w in words(name)):
            flags.append(Flag(0, False, f"referral name '{name}' is not in the reply"))
            name = ""
        r.referral_name = name
        if not ref:
            flags.append(Flag(0, False, "a referral without an email address; ask for it or look them up"))
    r.other_contacts = [a for a in named if a != r.referral_email]

    # the draft
    reply = t.reply.strip()
    if r.category in NO_REPLY:
        if reply:
            flags.append(Flag(0, False, f"no reply is sent to '{r.category}'; the model's draft was dropped"))
        reply = ""
    elif not reply:
        flags.append(Flag(0, True, "no draft reply; write one"))
    if reply:
        allowed = "\n".join([c.claims_text(), lead.facts_text() if lead else "", e.all_text()])
        check_text(reply, c, allowed, c.reply_max_words, 0, flags, allow_links=True)
        if r.category == "interested" and c.booking_link and c.booking_link not in reply:
            reply += f"\n\nHere's my calendar if that's easier: {c.booking_link}"
        reply += f"\n\n{c.sender_name}\n{c.sender_title}, {c.client}"
    r.reply = reply
    if t.needs_client:
        r.needs_client = t.needs_client_reason or "the client must answer part of this"
        flags.append(Flag(0, True, f"needs the client: {r.needs_client}"))
    return r
