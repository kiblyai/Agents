"""Keyword search (BM25) tuned for security wording, so each question gets the few passages that answer it."""

from __future__ import annotations

import math
import re
from collections import Counter

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


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(tokens(d)) for d in docs]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.docs else 0.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        q = tokens(query)
        out = []
        for d, length in zip(self.docs, self.lengths):
            s = 0.0
            for t in q:
                f = d.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / (self.avg or 1)))
            out.append(s)
        return out

    def top(self, query: str, k: int, min_score: float = 0.0, context: str = "", context_weight: float = 0.25
            ) -> list[tuple[int, float]]:
        """Best k documents for the query. `context` (e.g. the section title) only nudges the ranking."""
        scores = self.scores(query)
        if context:
            scores = [s + context_weight * c for s, c in zip(scores, self.scores(context))]
        ranked = sorted(enumerate(scores), key=lambda x: -x[1])
        return [(i, s) for i, s in ranked[:k] if s > min_score]
