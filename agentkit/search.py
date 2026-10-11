"""Keyword search (BM25): finds the few passages that answer a question. Agents can pass their own tokenizer."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Callable

STOP = set("""a an and are as at be by can do does for from has have how if in is it its of on or our the their this to
we what when where which who will with you your any all please describe provide list explain whether there other
yes no""".split())


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and (len(w) > 1 or w.isdigit())]


class BM25:
    def __init__(self, docs: list[str], tokenize: Callable[[str], list[str]] = tokens, k1: float = 1.5,
                 b: float = 0.75):
        self.tokenize = tokenize
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(d)) for d in docs]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.docs else 0.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        q = self.tokenize(query)
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
