"""Keyword search (BM25) tuned for security wording, so each question gets the few passages that answer it."""

from __future__ import annotations

import re

from agentkit.search import BM25 as _BM25

# Different words for the same control, folded into one token so questions and policies meet.
SYNONYMS: list[tuple[str, str]] = [
    (r"multi[- ]?factor(?: authentication)?|two[- ]factor(?: authentication)?|2fa", "mfa"),
    (r"single sign[- ]on", "sso"),
    (r"penetration test(?:ing|s)?|pen[- ]?test(?:ing|s)?", "pentest"),
    (r"sub[- ]?processors?", "subprocessor"),
    (r"at[- ]rest", "atrest"),
    (r"in[- ]transit", "intransit"),
    (r"role[- ]based access(?: control)?", "rbac"),
    (r"business[- ]continuity|disaster[- ]recovery", "bcdr"),
    (r"soc ?2|soc two", "soc2"),
    (r"iso ?27001", "iso27001"),
    (r"vulnerabilit(?:y|ies) scan(?:s|ning)?", "vulnscan"),
    (r"background[- ]checks?|background[- ]screening", "backgroundcheck"),
    (r"incident[- ]response", "incidentresponse"),
    (r"data[- ]retention|retention[- ]period", "retention"),
    (r"log(?:s|ging)?|audit[- ]trails?", "logging"),
]
STOP = set("""a an and are as at be by can do does for from has have how if in is it its of on or our the their this to
we what when where which who will with you your any all please describe provide list explain whether there any other
yes no""".split())


def normalize(text: str) -> str:
    t = text.lower()
    for pattern, token in SYNONYMS:
        t = re.sub(rf"\b(?:{pattern})\b", token, t)
    return t


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", normalize(text)) if w not in STOP and (len(w) > 1 or w.isdigit())]


class BM25(_BM25):
    """agentkit's BM25 with the security synonyms above."""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        super().__init__(docs, tokenize=tokens, k1=k1, b=b)
