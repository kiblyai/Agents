"""Contract deadlines in code: "ten (10) days after the Acceptance Date" becomes a date, with business days,
US federal holidays and the weekend roll rule."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from agentkit.checks import NUMBER_WORDS, find_dates


def _nth_weekday(y: int, month: int, weekday: int, n: int) -> date:
    """The nth weekday (0=Monday) of a month; n=-1 is the last."""
    if n > 0:
        d = date(y, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(y + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """A holiday on Saturday is observed on Friday, on Sunday on Monday."""
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def federal_holidays(y: int) -> dict[date, str]:
    days = {
        _observed(date(y, 1, 1)): "New Year's Day",
        _nth_weekday(y, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(y, 2, 0, 3): "Washington's Birthday",
        _nth_weekday(y, 5, 0, -1): "Memorial Day",
        _observed(date(y, 6, 19)): "Juneteenth",
        _observed(date(y, 7, 4)): "Independence Day",
        _nth_weekday(y, 9, 0, 1): "Labor Day",
        _nth_weekday(y, 10, 0, 2): "Columbus Day",
        _observed(date(y, 11, 11)): "Veterans Day",
        _nth_weekday(y, 11, 3, 4): "Thanksgiving Day",
        _observed(date(y, 12, 25)): "Christmas Day",
    }
    nxt = _observed(date(y + 1, 1, 1))  # next year's New Year's Day can be observed on Dec 31
    if nxt.year == y:
        days[nxt] = "New Year's Day (observed)"
    return days


class Calendar:
    def __init__(self, federal: bool = True, extra: list[date] | None = None):
        self.federal = federal
        self.extra = set(extra or [])
        self._cache: dict[int, dict[date, str]] = {}

    def holiday(self, d: date) -> str:
        if d in self.extra:
            return "holiday"
        if not self.federal:
            return ""
        if d.year not in self._cache:
            self._cache[d.year] = federal_holidays(d.year)
        return self._cache[d.year].get(d, "")

    def business_day(self, d: date) -> bool:
        return d.weekday() < 5 and not self.holiday(d)

    def add_business_days(self, d: date, n: int) -> date:
        step = 1 if n >= 0 else -1
        for _ in range(abs(n)):
            d += timedelta(days=step)
            while not self.business_day(d):
                d += timedelta(days=step)
        return d

    def next_business_day(self, d: date) -> date:
        while not self.business_day(d):
            d += timedelta(days=1)
        return d

    def why_not(self, d: date) -> str:
        return self.holiday(d) or d.strftime("%A")


# --- reading a deadline's wording ---------------------------------------------------------------------------------

ANCHORS = [
    ("acceptance", r"acceptance(?: date)?|the date of acceptance|effective date|binding agreement(?: date)?|"
                   r"mutual acceptance|execution(?: date)?|contract date|date of this agreement"),
    ("closing", r"closing(?: date)?|settlement(?: date)?|the close of escrow|close of escrow|consummation"),
]
NUM = (r"(\d+|(?:" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
       + r")(?:[- ](?:one|two|three|four|five|six|seven|eight|nine)\b)?)")
TERM_RE = re.compile(rf"\b{NUM}\s*(?:\(\s*\d+\s*\)\s*)?(business|banking|calendar|working)?\s*(days?|hours?)\s+"
                     r"(after|following|from|of|before|prior to|preceding)\s+(?:the\s+)?(.{0,80})", re.I)


def number(s: str) -> int | None:
    s = s.lower().strip()
    if s.isdigit():
        return int(s)
    parts = re.split(r"[- ]", s)
    total = 0
    for p in parts:
        if p not in NUMBER_WORDS:
            return None
        total += NUMBER_WORDS[p]
    return total


@dataclass
class Rule:
    kind: str  # "relative", "date", "event" (depends on something without a date) or "unknown"
    n: int = 0
    unit: str = "days"  # "days", "business days" or "hours"
    direction: int = 1  # +1 after, -1 before
    anchor: str = ""  # "acceptance" or "closing"
    on: date | None = None  # for kind "date"
    event: str = ""  # for kind "event", e.g. "receipt of the title commitment"


def parse_term(term: str, days_mean: str = "calendar") -> Rule:
    """'within three (3) business days after the Acceptance Date' -> Rule(relative, 3, business days, +1, acceptance)."""
    t = re.sub(r"\s+", " ", term or "").strip()
    m = TERM_RE.search(t)
    if m:
        n = number(m.group(1))
        unit = m.group(3).lower()
        kind_word = (m.group(2) or "").lower()
        if unit.startswith("hour"):
            unit = "hours"
        elif kind_word in ("business", "banking", "working") or (not kind_word and days_mean == "business"):
            unit = "business days"
        else:
            unit = "days"
        direction = -1 if m.group(4).lower() in ("before", "prior to", "preceding") else 1
        rest = m.group(5).lower()
        for name, rx in ANCHORS:
            if re.match(rf"(?:{rx})\b", rest):
                return Rule("relative", n or 0, unit, direction, name)
        return Rule("event", n or 0, unit, direction, event=m.group(5).strip(" .,;"))
    found = find_dates(t)
    if len(found) == 1:
        return Rule("date", on=found.pop())
    if re.search(r"\b(?:at|on|upon) (?:the )?closing\b|\bat settlement\b", t, re.I):
        return Rule("relative", 0, "days", 1, "closing")
    return Rule("unknown")


def compute(rule: Rule, anchors: dict[str, date | None], cal: Calendar, roll: bool) -> tuple[date | None, str]:
    """(the deadline, how it was worked out). Rolls a weekend or holiday to the next business day when told to."""
    if rule.kind == "date":
        d, how = rule.on, "date written in the contract"
    elif rule.kind == "relative":
        base = anchors.get(rule.anchor)
        if base is None:
            return None, f"needs the {rule.anchor} date"
        if rule.unit == "business days":
            d = cal.add_business_days(base, rule.direction * rule.n)
        elif rule.unit == "hours":
            d = base + timedelta(days=rule.direction * -(-rule.n // 24))
        else:
            d = base + timedelta(days=rule.direction * rule.n)
        side = "after" if rule.direction > 0 else "before"
        how = f"{rule.n} {rule.unit} {side} {rule.anchor} ({base.isoformat()})" if rule.n else f"at {rule.anchor}"
    else:
        return None, (f"depends on {rule.event}: set it when that happens" if rule.kind == "event"
                      else "could not work out a date from the wording")
    if roll and d and not cal.business_day(d) and rule.unit != "business days":
        moved = cal.next_business_day(d)
        how += f"; {d.isoformat()} is {cal.why_not(d)}, so it moves to {moved.isoformat()}"
        d = moved
    return d, how


# --- the contract's own rules about time --------------------------------------------------------------------------

def time_rules(text: str) -> tuple[str, bool | None]:
    """(what a bare "days" means, whether deadlines on weekends/holidays roll) as the contract states them."""
    low = re.sub(r"\s+", " ", text.lower())
    days_mean = ""
    if re.search(r"\bdays?\W{1,3}(?:shall )?means? (?:consecutive )?calendar days", low):
        days_mean = "calendar"
    elif re.search(r"\bdays?\W{1,3}(?:shall )?means? (?:business|banking) days", low):
        days_mean = "business"
    roll = None
    if re.search(r"(?:saturday|sunday|weekend|legal holiday).{0,120}(?:extended|extends|moves?|shall be) "
                 r"(?:to )?(?:the )?next business day", low):
        roll = True
    elif re.search(r"(?:shall not|will not|is not) (?:be )?extended", low):
        roll = False
    return days_mean, roll


# --- the price written in words -----------------------------------------------------------------------------------

SCALES = {"thousand": 1_000, "million": 1_000_000}


def words_to_number(s: str) -> int | None:
    """'Four Hundred Twenty-Five Thousand Dollars' -> 425000. None if it isn't a number in words."""
    ws = [w for w in re.findall(r"[a-z]+", (s or "").lower()) if w not in ("and", "dollars", "dollar", "us", "no",
                                                                             "exactly", "only")]
    if not ws:
        return None
    total = current = 0
    for w in ws:
        if w in NUMBER_WORDS and w != "hundred":
            current += NUMBER_WORDS[w]
        elif w == "hundred":
            current = (current or 1) * 100
        elif w in SCALES:
            total += (current or 1) * SCALES[w]
            current = 0
        else:
            return None
    return total + current
