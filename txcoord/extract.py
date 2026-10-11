"""Reads a purchase contract and its addenda with the model: terms and each deadline's wording, copied as written."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

SYSTEM = """You read a residential real-estate purchase contract and its addenda for a transaction coordinator.

Rules:
- Copy every term as written. Never work out a deadline date yourself: give its wording ("term") exactly as the contract states it, e.g. "within ten (10) days after the Acceptance Date", plus the full sentence it comes from ("quote").
- Give the FINAL terms after all addenda and counteroffers. List every change an addendum makes in "changes", with the document's file name and the sentence that makes the change.
- deadlines: every contingency and date-driven task: earnest money, inspection, appraisal, financing or loan commitment, HOA or condo documents, title, seller disclosures, sale of buyer's home, final walk-through, possession, and anything else with a time limit. party: who must act ("buyer", "seller", "lender", "title" or "both").
- Dates as YYYY-MM-DD. Money as numbers. price_in_words: the price exactly as the contract spells it in words, or "".
- contacts: agents, lender, title or escrow company and attorneys, with emails only as written.
- Reply with one JSON object only."""

SCHEMA_HINT = """{"property": "<address>", "buyers": ["<name>"], "sellers": ["<name>"], "price": <number>, "price_in_words": "<as written or ''>", "earnest_money": <number or null>, "acceptance_date": "<YYYY-MM-DD>", "closing_date": "<YYYY-MM-DD>", "financing": "<cash|conventional|FHA|VA|...>", "loan_amount": <number or null>, "down_payment": <number or null>, "deadlines": [{"name": "<e.g. Inspection period>", "term": "<the wording of the time limit>", "quote": "<the full sentence>", "party": "<buyer|seller|lender|title|both>"}], "changes": [{"document": "<file name>", "term": "<what changed>", "new_value": "<new value as written>", "quote": "<the sentence>"}], "contacts": [{"role": "<buyer's agent|listing agent|lender|title|attorney>", "name": "<...>", "company": "<...>", "email": "<...>"}]}"""

MAX_DOC = 30000  # characters of any one document sent to the model


def build_prompt(folder: str, docs: list[tuple[str, str]]) -> str:
    blocks = [f"Transaction folder: {folder}"]
    for name, text in docs:
        body = text if len(text) <= MAX_DOC else text[:MAX_DOC] + "\n[... cut ...]"
        blocks.append(f"### Document: {name}\n{body}")
    return "\n\n".join(blocks) + f"\n\nReturn JSON in exactly this shape (replace every <...>):\n{SCHEMA_HINT}"


def _str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(str(x) for x in v if x not in (None, ""))
    return str(v).strip()


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(v))
    return float(m.group(0).replace(",", "")) if m else None


class DeadlineReading(BaseModel):
    name: str = ""
    term: str = ""
    quote: str = ""
    party: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)


class Change(BaseModel):
    document: str = ""
    term: str = ""
    new_value: str = ""
    quote: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)


class Contact(BaseModel):
    role: str = ""
    name: str = ""
    company: str = ""
    email: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)


class ContractReading(BaseModel):
    property: str = ""
    buyers: list[str] = Field(default_factory=list)
    sellers: list[str] = Field(default_factory=list)
    price: float | None = None
    price_in_words: str = ""
    earnest_money: float | None = None
    acceptance_date: str = ""
    closing_date: str = ""
    financing: str = ""
    loan_amount: float | None = None
    down_payment: float | None = None
    deadlines: list[DeadlineReading] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)

    @field_validator("property", "price_in_words", "acceptance_date", "closing_date", "financing", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("buyers", "sellers", mode="before")
    @classmethod
    def _names(cls, v):
        if v is None or v == "":
            return []
        items = v if isinstance(v, list) else re.split(r";|\band\b", str(v))
        return [_str(x) for x in items if _str(x)]

    @field_validator("price", "earnest_money", "loan_amount", "down_payment", mode="before")
    @classmethod
    def _n(cls, v):
        return _num(v)

    @field_validator("deadlines", "changes", "contacts", mode="before")
    @classmethod
    def _l(cls, v):
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []
