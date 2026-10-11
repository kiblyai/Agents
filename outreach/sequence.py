"""Writes each lead's email sequence with the model, then checks every email in code before it can be sent."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator

from agentkit.checks import find_phrases, quote_found, unsupported_numbers, word_count, words

from .campaign import Campaign
from .leads import Lead

SYSTEM = """You write short, plain cold emails for {client}, a B2B service firm, to one person at a time.

Rules:
- Write exactly {steps} emails. Email 1 starts the conversation. The later ones are short follow-ups sent in the same thread, so they have no subject.
- Email 1 opens with "Hi <first name>," and then one sentence about the lead's company, built on ONE fact from LEAD FACTS. Copy that fact word for word into personal_fact. Prefer the recent signal; without one, use the industry or city.
- Say what the client does in plain words, using only OFFER and PROOF. Cite the proof you use by id in proof_used. Never invent numbers, clients, results, prices or guarantees.
- End email 1 with the call to action given. Follow-ups add one new reason to talk (another proof point), never repeat email 1.
- {links}
- No signature, sign-off name or unsubscribe line: they are added for you. No placeholders like {{first_name}}.
- Keep each email under its word limit. Never use these phrases: {banned}.
- Reply with one JSON object only."""

SCHEMA_HINT = """{"subject": "<email 1 subject, 2-6 words, no 'Re:'>", "personal_fact": "<the lead fact email 1 opens with, copied word for word>", "emails": [{"step": 1, "body": "<email 1, starting 'Hi <first name>,'>", "proof_used": ["P1"]}, {"step": 2, "body": "<...>", "proof_used": ["P2"]}]}"""


def system_prompt(c: Campaign) -> str:
    links = ("No links in email 1. Later emails may include the booking link, exactly as given." if
             not c.links_in_first_email else "Use no links except the booking link, exactly as given.")
    return SYSTEM.format(client=c.client, steps=c.steps, links=links, banned="; ".join(f'"{b}"' for b in c.banned))


def build_prompt(lead: Lead, c: Campaign) -> str:
    limits = ", ".join(f"email {i}: {n} words" for i, n in enumerate(c.max_words, 1))
    facts = "\n".join(f"- {k}: {v}" for k, v in lead.facts())
    return (f"LEAD FACTS\n{facts}\n\n"
            f"CLIENT: {c.client} ({c.website})\nSENDER: {c.sender_name}, {c.sender_title}\n"
            f"OFFER: {c.offer}\nPROOF:\n{c.proof_block() or '(none: make no claims about results)'}\n"
            f"CALL TO ACTION: {c.cta}\nBOOKING LINK: {c.booking_link or '(none)'}\n"
            f"WORD LIMITS: {limits}\n\n"
            f"Return JSON in exactly this shape (replace every <...>):\n{SCHEMA_HINT}")


def _str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "\n".join(str(x) for x in v if x not in (None, ""))
    return str(v).strip()


def _ids(v) -> list[str]:
    if v is None or v == "":
        return []
    items = v if isinstance(v, list) else re.split(r"[,;\s]+", str(v))
    return [str(x).strip().upper() for x in items if str(x).strip()]


class DraftEmail(BaseModel):
    step: int = 0
    body: str = ""
    proof_used: list[str] = Field(default_factory=list)

    @field_validator("body", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("proof_used", mode="before")
    @classmethod
    def _p(cls, v):
        return _ids(v)

    @field_validator("step", mode="before")
    @classmethod
    def _n(cls, v):
        m = re.search(r"\d+", str(v or ""))
        return int(m.group(0)) if m else 0


class SequenceDraft(BaseModel):
    subject: str = ""
    personal_fact: str = ""
    emails: list[DraftEmail] = Field(default_factory=list)

    @field_validator("subject", "personal_fact", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("emails", mode="before")
    @classmethod
    def _list(cls, v):
        if isinstance(v, dict):
            v = list(v.values())
        return [x if isinstance(x, dict) else {"body": x} for x in v] if isinstance(v, list) else []


@dataclass
class Flag:
    step: int  # 0 = the whole sequence
    hold: bool  # True: must be fixed before sending; False: worth a look
    text: str


@dataclass
class Sequence:
    lead: Lead
    subject: str = ""
    personal_fact: str = ""
    bodies: list[str] = field(default_factory=list)  # final text, footer included
    proof_used: list[list[str]] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    error: str = ""

    @property
    def held(self) -> bool:
        return bool(self.error or self.lead.held or any(f.hold for f in self.flags))


PLACEHOLDER_RE = re.compile(r"\{[^}\n]{0,40}\}|\[\[?[A-Z_ ]{3,40}\]?\]|<[^>\n]{2,40}>|\b(?:FIRST_NAME|COMPANY_NAME)\b")
URL_RE = re.compile(r"(?:https?://|www\.)[^\s)>\]]+", re.I)
FAKE_THREAD_RE = re.compile(r"^\s*(?:re|fwd?|fw)\s*:", re.I)


def _norm_url(u: str) -> str:
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", u.strip().rstrip(".,;)").lower()).rstrip("/")


def allowed_link(url: str, c: Campaign) -> bool:
    u = _norm_url(url)
    ok = [_norm_url(x) for x in (c.booking_link, c.website) if x]
    return any(u == x or (x and u.startswith(x + "/")) for x in ok)


def repeated_run(a: str, b: str, n: int = 8) -> bool:
    """True if the texts share a run of n words in a row (a follow-up copying an earlier email)."""
    wa, wb = words(a), words(b)
    grams = {tuple(wa[i:i + n]) for i in range(len(wa) - n + 1)}
    return any(tuple(wb[i:i + n]) in grams for i in range(len(wb) - n + 1))


def check_text(text: str, c: Campaign, allowed_facts: str, limit: int, step: int, flags: list[Flag],
               allow_links: bool) -> None:
    """The checks every outgoing email gets: placeholders, length, banned phrases, numbers and links."""
    if not text.strip():
        flags.append(Flag(step, True, "empty email"))
        return
    if PLACEHOLDER_RE.search(text):
        flags.append(Flag(step, True, f"unfilled placeholder '{PLACEHOLDER_RE.search(text).group(0)}'"))
    n = word_count(text)
    if n > limit * 1.25:
        flags.append(Flag(step, True, f"{n} words; the limit is {limit}"))
    elif n > limit:
        flags.append(Flag(step, False, f"{n} words; the limit is {limit}"))
    banned = find_phrases(text, c.banned)
    if banned:
        flags.append(Flag(step, True, "banned phrase: " + ", ".join(f"'{b}'" for b in banned)))
    nums = unsupported_numbers(text, allowed_facts)
    if nums:
        flags.append(Flag(step, True, f"mentions {', '.join(nums)}, which is not in the offer, proof or lead data"))
    for url in URL_RE.findall(text):
        if not allowed_link(url, c):
            flags.append(Flag(step, True, f"link {url} is not the client's booking link or website"))
        elif not allow_links:
            flags.append(Flag(step, True, "a link in the first email (hurts deliverability); remove it"))


def check_sequence(d: SequenceDraft, lead: Lead, c: Campaign) -> Sequence:
    seq = Sequence(lead=lead, subject=d.subject.strip().strip('"'), personal_fact=d.personal_fact.strip())
    flags = seq.flags
    facts = lead.facts_text()
    allowed = "\n".join([facts, c.claims_text()])
    proof_ids = {f"P{i}" for i in range(1, len(c.proof) + 1)}
    if d.emails and all(e.step for e in d.emails):
        by_step = {e.step: e for e in d.emails}
        ordered = [by_step.get(i) for i in range(1, c.steps + 1)]
    else:  # no step numbers: take them in order
        ordered = [d.emails[i] if i < len(d.emails) else None for i in range(c.steps)]
    # subject
    if not seq.subject:
        flags.append(Flag(1, True, "no subject"))
    else:
        if FAKE_THREAD_RE.match(seq.subject):
            flags.append(Flag(1, True, "subject starts with 'Re:' or 'Fwd:' on a first email (deceptive)"))
        if word_count(seq.subject) > c.max_subject_words:
            flags.append(Flag(1, False, f"subject has {word_count(seq.subject)} words; keep it to {c.max_subject_words}"))
        banned = find_phrases(seq.subject, c.banned)
        if banned:
            flags.append(Flag(1, True, "banned phrase in the subject: " + ", ".join(banned)))
        nums = unsupported_numbers(seq.subject, allowed)
        if nums:
            flags.append(Flag(1, True, f"subject mentions {', '.join(nums)}, which is not in the data"))
    # the personal fact must be the lead's, and email 1 must use it
    if not seq.personal_fact or not quote_found(seq.personal_fact, facts, 0.75):
        flags.append(Flag(1, True, "the opening fact is not in the lead's data" +
                          (f": '{seq.personal_fact}'" if seq.personal_fact else "")))
    for i, e in enumerate(ordered, 1):
        body = (e.body if e else "").strip()
        if not body:
            flags.append(Flag(i, True, f"email {i} is missing"))
            seq.bodies.append("")
            seq.proof_used.append([])
            continue
        unknown = [p for p in (e.proof_used if e else []) if p not in proof_ids]
        if unknown:
            flags.append(Flag(i, False, f"cites proof {', '.join(unknown)} that does not exist"))
        check_text(body, c, allowed, c.max_words[i - 1], i, flags, allow_links=i > 1 or c.links_in_first_email)
        if i == 1:
            if lead.first_name and lead.first_name.lower() not in body.lower()[:60]:
                flags.append(Flag(1, False, f"email 1 does not greet {lead.first_name} by name"))
            fact_words = {w for w in words(seq.personal_fact) if len(w) > 3}
            if fact_words and len(fact_words & set(words(body))) < min(2, len(fact_words)):
                flags.append(Flag(1, False, "email 1 does not seem to use the opening fact"))
        for j, earlier in enumerate(seq.bodies, 1):
            if earlier and repeated_run(earlier, body):
                flags.append(Flag(i, False, f"email {i} repeats email {j}"))
        seq.bodies.append(body)
        seq.proof_used.append([p for p in (e.proof_used if e else []) if p in proof_ids])
    footer = c.footer_text()
    seq.bodies = [f"{b}\n\n{footer}" if b else "" for b in seq.bodies]
    return seq
