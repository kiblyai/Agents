"""Checks the agents run in code on what a model wrote: numbers, money, dates and quotes must be in the source."""

from __future__ import annotations

import re
from datetime import date, timedelta

# --- numbers and money --------------------------------------------------------------------------------------------

NUM_RE = re.compile(r"(?<![\d.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
SCALED_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(k|thousand|m|mm|million|b|bn|billion)\b", re.I)
SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
                "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
                "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100}


def amounts(text: str) -> set[float]:
    """Every number the text states: "1,250.50" -> 1250.5, "$4.5M" -> 4500000, "twelve" -> 12, "25%" -> 25."""
    t = text or ""
    out = {float(n.replace(",", "")) for n in NUM_RE.findall(t)}
    out |= {float(n) * SCALE[s.lower()] for n, s in SCALED_RE.findall(t)}
    out |= {float(NUMBER_WORDS[w]) for w in re.findall(r"[a-z]+", t.lower()) if w in NUMBER_WORDS}
    return out


def has_amount(x: float, text: str, tolerance: float = 0.005) -> bool:
    return any(abs(x - a) <= tolerance for a in amounts(text))


def unsupported_numbers(claim: str, source: str) -> list[str]:
    """Numbers in the claim that the source never states. Whole values: "3" is not backed by "35 days", and
    "$5M" needs 5,000,000 (written any way) in the source."""
    have = amounts(source)

    def backed(x: float) -> bool:
        return any(abs(x - a) < 0.005 for a in have)

    out, scaled = [], []
    for m in SCALED_RE.finditer(claim or ""):
        scaled.append(range(m.start(), m.end()))
        if not backed(float(m.group(1)) * SCALE[m.group(2).lower()]):
            out.append(m.group(0).replace(" ", ""))
    for m in NUM_RE.finditer(claim or ""):
        if any(m.start() in r for r in scaled):
            continue
        if not backed(float(m.group(0).replace(",", ""))):
            out.append(m.group(0))
    return list(dict.fromkeys(out))


def money(x: float | None, cents: bool = False) -> str:
    if x is None:
        return ""
    return f"${x:,.2f}" if cents else f"${x:,.0f}"


def parse_money(s) -> float | None:
    """'$1,250.00', '1250', 'USD 1,250' -> 1250.0; '' or text without a number -> None."""
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return float(s)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(s or ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


# --- dates --------------------------------------------------------------------------------------------------------

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
          "oct": 10, "nov": 11, "dec": 12}
MON = (r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|"
       r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?")
DATE_RES = [
    ("ymd", re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")),
    ("mdy", re.compile(r"(?<![\d/$.])(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})(?![\d/-])")),
    ("md", re.compile(r"(?<![\d/$.])(\d{1,2})/(\d{1,2})(?![\d/])")),
    ("Mdy", re.compile(rf"\b{MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I)),
    ("dMy", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+day\s+of)?[\s-]+{MON}(?:[\s,-]+(\d{{4}}))?\b", re.I)),
]


def _month(word: str) -> int:
    w = word.lower().rstrip(".")
    return MONTHS.get(w[:4] if w.startswith("sept") else w[:3], 0)


def year_for(mo: int, d: int, ref: date, lean_days: int = 60) -> date:
    """A date written without a year: the one nearest `ref`, leaning forward (deadlines come after the letter)."""
    x = date(ref.year, mo, d)
    if (ref - x).days > lean_days:
        x = date(ref.year + 1, mo, d)
    elif (x - ref).days > 365 - lean_days:
        x = date(ref.year - 1, mo, d)
    return x


def find_dates(text: str, ref: date | None = None) -> set[date]:
    """Every date written in the text (US month-first). Dates without a year need `ref`; others are skipped."""
    out: set[date] = set()
    for kind, rx in DATE_RES:
        for m in rx.finditer(text or ""):
            g = m.groups()
            try:
                if kind == "ymd":
                    out.add(date(int(g[0]), int(g[1]), int(g[2])))
                elif kind == "mdy":
                    y = int(g[2]) if len(g[2]) == 4 else 2000 + int(g[2])
                    out.add(date(y, int(g[0]), int(g[1])))
                elif kind == "md":
                    if ref:
                        out.add(year_for(int(g[0]), int(g[1]), ref))
                elif kind == "Mdy":
                    mo, d = _month(g[0]), int(g[1])
                    if g[2]:
                        out.add(date(int(g[2]), mo, d))
                    elif ref:
                        out.add(year_for(mo, d, ref))
                else:
                    d, mo = int(g[0]), _month(g[1])
                    if g[2]:
                        out.add(date(int(g[2]), mo, d))
                    elif ref:
                        out.add(year_for(mo, d, ref))
            except ValueError:
                continue
    return out


def parse_date(s: str, ref: date | None = None) -> date | None:
    """One date from a value like '2026-10-15', '10/15/2026' or 'October 15, 2026'; None if unclear."""
    s = (s or "").strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    found = find_dates(s, ref)
    return found.pop() if len(found) == 1 else None


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_RE = re.compile(r"\b(next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tues?|wed|"
                        r"thu(?:rs?)?|fri)\b\.?", re.I)


def relative_dates(text: str, ref: date) -> set[date]:
    """'today', 'tomorrow', 'next week' and weekday names, read from the day `ref` the message was sent.
    An ambiguous 'next Tuesday' gives both candidates; a weekday named on that same weekday gives today and a
    week later."""
    out: set[date] = set()
    low = (text or "").lower()
    if re.search(r"\btoday\b", low):
        out.add(ref)
    if re.search(r"\btomorrow\b", low):
        out.add(ref + timedelta(days=1))
    if re.search(r"\bnext week\b", low):
        out.add(ref + timedelta(days=7 - ref.weekday()))  # the Monday after
    for m in WEEKDAY_RE.finditer(low):
        wd = next(i for i, name in enumerate(WEEKDAYS) if name.startswith(m.group(2)[:3]))
        ahead = (wd - ref.weekday()) % 7
        if m.group(1):
            first = ahead or 7
            out |= {ref + timedelta(days=first), ref + timedelta(days=first + 7)}
        elif ahead:
            out.add(ref + timedelta(days=ahead))
        else:
            out |= {ref, ref + timedelta(days=7)}
    return out


# --- words and quotes ---------------------------------------------------------------------------------------------

def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", (text or "").lower())


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def norm_text(text: str) -> str:
    """Lowercase, plain quotes and dashes, single spaces: for comparing a quote with its source."""
    t = (text or "").lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[‐-―]", "-", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9$%.,'/-]+", " ", t)).strip()


def quote_found(quote: str, text: str, min_share: float = 0.85) -> bool:
    """True when the quote is in the text: word for word, or nearly (every number, and most words, in order)."""
    q, t = norm_text(quote), norm_text(text)
    if not q:
        return False
    if q.strip(" .,") in t:
        return True
    if unsupported_numbers(quote, text):
        return False
    qw = [w for w in words(q) if len(w) > 2]
    if not qw:
        return False
    tw = set(words(t))
    return sum(1 for w in qw if w in tw) / len(qw) >= min_share


def find_phrases(text: str, phrases: list[str]) -> list[str]:
    """Which of the phrases appear in the text (whole words, any case)."""
    low = " " + norm_text(text) + " "
    return [p for p in phrases if re.search(rf"(?<![a-z0-9]){re.escape(norm_text(p))}(?![a-z0-9])", low)]


# --- contact details ----------------------------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def emails_in(text: str) -> list[str]:
    return list(dict.fromkeys(e.lower().rstrip(".") for e in EMAIL_RE.findall(text or "")))


def valid_email(s: str) -> bool:
    s = (s or "").strip()
    return bool(EMAIL_RE.fullmatch(s)) and ".." not in s and not s.startswith(".")
