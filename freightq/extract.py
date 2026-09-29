"""Reads each email into shipments with the model, then checks every value against the email's own text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from pydantic import BaseModel, Field, field_validator

from .broker import BrokerConfig
from .inbox import MAX_PART, Email

SYSTEM = """You read emails sent to a US freight brokerage and turn each one into shipment data for quoting and order entry.

Rules:
- kind: "quote_request" when the sender asks for a price; "tender" when they book or tender a load to the broker (a load tender, "please book", "you're covering"); "other" for anything else (a carrier offering trucks, invoices, tracking questions, newsletters).
- One shipment per lane. An email asking about 3 lanes has 3 shipments. When a newer message in a thread changes an earlier one, use the newer value.
- Use only what the email and its attachments say. Never guess a ZIP code, state, date, weight, temperature, rate or reference number. Use "" (or null for numbers) when the email doesn't give it.
- origin and destination: city, state (2-letter code) and ZIP code as written. extra_stops: stops between the first pickup and the final delivery, in order.
- Dates as YYYY-MM-DD. Work out words like "tomorrow" or "Thursday" from the date the email was sent. Times as written, e.g. "8am-2pm".
- equipment: one of "van", "reefer", "flatbed", "step deck", "conestoga", "power only", "box truck"; "" if the email doesn't say.
- weight_lbs: pounds as a number (42k = 42000). pieces: count and unit as written, e.g. "22 pallets". temperature: as written, e.g. "34F".
- hazmat: true only when the email says the freight is hazardous.
- requirements: special needs as written, e.g. "tarps", "team drivers", "liftgate at delivery", "TWIC card".
- references: load, PO, BOL and pickup numbers as written, e.g. "PO 4471-A".
- rate: a dollar amount the sender gives for the load (a tender rate or a target rate), else null.
- truckloads: how many truckloads of this lane (usually 1).
- customer: the sender's company. contact: the sender's name.
- Reply with one JSON object only."""

SCHEMA_HINT = """{"kind": "<quote_request|tender|other>", "customer": "<company>", "contact": "<name>", "shipments": [{"origin": {"city": "<...>", "state": "<XX>", "zip": "<...>"}, "destination": {"city": "<...>", "state": "<XX>", "zip": "<...>"}, "extra_stops": [{"city": "<...>", "state": "<XX>", "zip": "<...>"}], "pickup_date": "<YYYY-MM-DD>", "pickup_time": "<as written>", "delivery_date": "<YYYY-MM-DD>", "delivery_time": "<as written>", "equipment": "<...>", "weight_lbs": <number or null>, "commodity": "<...>", "pieces": "<...>", "temperature": "<...>", "hazmat": <true|false>, "requirements": ["<...>"], "references": ["<...>"], "rate": <number or null>, "truckloads": <number>}]}"""


def _clip(text: str) -> str:
    return text if len(text) <= MAX_PART else text[:MAX_PART] + "\n[... cut ...]"


def build_prompt(e: Email) -> str:
    sent = e.sent.strftime("%A %Y-%m-%d %H:%M") if e.sent else "unknown"
    head = [f"Email file: {e.file}", f"Sent: {sent}", f"From: {e.sender_name} <{e.sender}>".replace(" <>", ""),
            f"Subject: {e.subject}"]
    blocks = ["\n".join(head), "### Newest message\n" + _clip(e.latest or "(empty)")]
    if e.earlier:
        blocks.append("### Earlier messages in the thread (context; the newest message wins)\n" + _clip(e.earlier))
    blocks += [f"### Attachment: {a.name}\n{_clip(a.text)}" for a in e.attachments]
    return "\n\n".join(blocks) + f"\n\nReturn JSON in exactly this shape (replace every <...>):\n{SCHEMA_HINT}"


# --- tolerant reply schema ----------------------------------------------------------------------------------------

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
    s = str(v).lower().replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    x = float(m.group(0))
    return x * 1000 if re.match(r"\s*k\b", s[m.end():]) else x


def _list(v) -> list[str]:
    if v is None or v == "":
        return []
    items = v if isinstance(v, list) else re.split(r"[;,]", str(v))
    return [_str(x) for x in items if _str(x)]


class Place(BaseModel):
    city: str = ""
    state: str = ""
    zip: str = ""

    @field_validator("city", "state", "zip", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)


def _place(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():  # "Dallas, TX 75207"
        m = re.match(r"\s*(.*?)[,\s]+([A-Za-z]{2})\.?\s*(\d{5})?\s*$", v)
        return {"city": m.group(1), "state": m.group(2), "zip": m.group(3) or ""} if m else {"city": v}
    return {}


class ExtractedShipment(BaseModel):
    origin: Place = Field(default_factory=Place)
    destination: Place = Field(default_factory=Place)
    extra_stops: list[Place] = Field(default_factory=list)
    pickup_date: str = ""
    pickup_time: str = ""
    delivery_date: str = ""
    delivery_time: str = ""
    equipment: str = ""
    weight_lbs: float | None = None
    commodity: str = ""
    pieces: str = ""
    temperature: str = ""
    hazmat: bool = False
    requirements: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    rate: float | None = None
    truckloads: int = 1

    @field_validator("origin", "destination", mode="before")
    @classmethod
    def _p(cls, v):
        return _place(v)

    @field_validator("extra_stops", mode="before")
    @classmethod
    def _stops(cls, v):
        return [_place(x) for x in v if _place(x)] if isinstance(v, list) else []

    @field_validator("pickup_date", "pickup_time", "delivery_date", "delivery_time", "equipment", "commodity",
                     "pieces", "temperature", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("weight_lbs", "rate", mode="before")
    @classmethod
    def _n(cls, v):
        return _num(v)

    @field_validator("hazmat", mode="before")
    @classmethod
    def _b(cls, v):
        return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1", "y")

    @field_validator("requirements", "references", mode="before")
    @classmethod
    def _l(cls, v):
        return _list(v)

    @field_validator("truckloads", mode="before")
    @classmethod
    def _loads(cls, v):
        n = _num(v)
        return int(n) if n and n >= 1 else 1


class EmailReading(BaseModel):
    kind: str = "other"
    customer: str = ""
    contact: str = ""
    shipments: list[ExtractedShipment] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        v = re.sub(r"[\s-]+", "_", str(v or "").strip().lower())
        v = {"quote": "quote_request", "rfq": "quote_request", "load_tender": "tender"}.get(v, v)
        return v if v in ("quote_request", "tender", "other") else "other"

    @field_validator("customer", "contact", mode="before")
    @classmethod
    def _s(cls, v):
        return _str(v)

    @field_validator("shipments", mode="before")
    @classmethod
    def _ships(cls, v):
        return [x for x in v if isinstance(x, (dict, ExtractedShipment))] if isinstance(v, list) else []


# --- reading dates, numbers and places from email text -------------------------------------------------------------

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
          "nov": 11, "dec": 12}
MON = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|"
       r"nov(?:ember)?|dec(?:ember)?)\.?")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_RE = re.compile(r"\b(?:(next|this)\s+)?(mon|tues?|wed(?:nes)?|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:day)?\b", re.I)
DATE_RES = [
    ("ymd", re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")),
    ("mdy", re.compile(r"(?<![\d/$.])(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})(?![\d/-])")),
    ("md", re.compile(r"(?<![\d/$.])(\d{1,2})/(\d{1,2})(?![\d/])")),
    ("Mdy", re.compile(rf"\b{MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I)),
    ("dMy", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s-]+{MON}(?:[\s,-]+(\d{{4}}))?\b", re.I)),
]


def _year_for(mo: int, d: int, ref: date) -> date:
    """A date written without a year: the one nearest the email, leaning forward (loads ship after the email)."""
    x = date(ref.year, mo, d)
    if (ref - x).days > 60:
        x = date(ref.year + 1, mo, d)
    elif (x - ref).days > 300:
        x = date(ref.year - 1, mo, d)
    return x


def find_dates(text: str, ref: date) -> set[date]:
    """Every date the text can mean: written dates (US month-first), and "today", "tomorrow" or weekday names read
    from the day the email was sent. An ambiguous "next Tuesday" gives both candidates."""
    out: set[date] = set()
    for kind, rx in DATE_RES:
        for m in rx.finditer(text):
            g = m.groups()
            try:
                if kind == "ymd":
                    out.add(date(int(g[0]), int(g[1]), int(g[2])))
                elif kind == "mdy":
                    y = int(g[2]) if len(g[2]) == 4 else 2000 + int(g[2])
                    out.add(date(y, int(g[0]), int(g[1])))
                elif kind == "md":
                    out.add(_year_for(int(g[0]), int(g[1]), ref))
                elif kind == "Mdy":
                    mo, d = MONTHS[g[0][:3].lower()], int(g[1])
                    out.add(date(int(g[2]), mo, d) if g[2] else _year_for(mo, d, ref))
                else:
                    d, mo = int(g[0]), MONTHS[g[1][:3].lower()]
                    out.add(date(int(g[2]), mo, d) if g[2] else _year_for(mo, d, ref))
            except (ValueError, KeyError):
                continue
    low = text.lower()
    if re.search(r"\b(?:today|tonight|same[\s-]day)\b", low):
        out.add(ref)
    if re.search(r"\b(?:tomorrow|tmrw|tmw)\b", low):
        out.add(ref + timedelta(days=1))
    for m in WEEKDAY_RE.finditer(text):
        word = m.group(2).lower()
        wd = next(i for i, name in enumerate(WEEKDAYS) if name.startswith(word[:3]))
        ahead = (wd - ref.weekday()) % 7
        if m.group(1) and m.group(1).lower() == "next":
            out |= {ref + timedelta(days=ahead or 7), ref + timedelta(days=(ahead or 7) + 7)}
        elif ahead:
            out.add(ref + timedelta(days=ahead))
        else:
            out |= {ref, ref + timedelta(days=7)}
    return out


def parse_date(s: str, ref: date | None = None) -> date | None:
    s = (s or "").strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    found = find_dates(s, ref or date.today()) if s else set()
    return found.pop() if len(found) == 1 else None


NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def amounts(text: str) -> set[float]:
    """Numbers in the text: "38,500" -> 38500, "42k" -> 42000, "21 tons" -> 42000."""
    out = {float(n.replace(",", "")) for n in NUM_RE.findall(text)}
    out |= {float(n) * 1000 for n in re.findall(r"(\d+(?:\.\d+)?)\s*k\b", text, re.I)}
    out |= {float(n) * 2000 for n in re.findall(r"(\d+(?:\.\d+)?)\s*tons?\b", text, re.I)}
    return out


NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def has_amount(x: float, text: str) -> bool:
    return any(abs(x - a) < 0.005 for a in amounts(text)) or any(
        NUMBER_WORDS.get(w) == x for w in re.findall(r"[a-z]+", text.lower()))


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    # Canadian provinces, for cross-border loads (no postal-code check)
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick", "NL": "Newfoundland",
    "NS": "Nova Scotia", "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan",
}
STATE_BY_NAME = {v.lower(): k for k, v in STATES.items()}

# First three ZIP digits by state (USPS prefixes). Only used to flag a ZIP that doesn't match its state.
ZIP3_RANGES = [
    (5, 5, "NY"), (10, 27, "MA"), (28, 29, "RI"), (30, 38, "NH"), (39, 49, "ME"), (50, 54, "VT"), (55, 55, "MA"),
    (56, 59, "VT"), (60, 69, "CT"), (70, 89, "NJ"), (100, 149, "NY"), (150, 196, "PA"), (197, 199, "DE"),
    (200, 200, "DC"), (201, 201, "VA"), (202, 205, "DC"), (206, 219, "MD"), (220, 246, "VA"), (247, 268, "WV"),
    (270, 289, "NC"), (290, 299, "SC"), (300, 319, "GA"), (320, 339, "FL"), (341, 349, "FL"), (350, 369, "AL"),
    (370, 385, "TN"), (386, 397, "MS"), (398, 399, "GA"), (400, 427, "KY"), (430, 459, "OH"), (460, 479, "IN"),
    (480, 499, "MI"), (500, 528, "IA"), (530, 549, "WI"), (550, 567, "MN"), (569, 569, "DC"), (570, 577, "SD"),
    (580, 588, "ND"), (590, 599, "MT"), (600, 629, "IL"), (630, 658, "MO"), (660, 679, "KS"), (680, 693, "NE"),
    (700, 714, "LA"), (716, 729, "AR"), (730, 732, "OK"), (733, 733, "TX"), (734, 749, "OK"), (750, 799, "TX"),
    (800, 816, "CO"), (820, 831, "WY"), (832, 838, "ID"), (840, 847, "UT"), (850, 865, "AZ"), (870, 884, "NM"),
    (885, 885, "TX"), (889, 898, "NV"), (900, 961, "CA"), (967, 968, "HI"), (970, 979, "OR"), (980, 994, "WA"),
    (995, 999, "AK"),
]


def zip_state(z: str) -> str:
    if not re.fullmatch(r"\d{5}", z or ""):
        return ""
    n = int(z[:3])
    return next((s for lo, hi, s in ZIP3_RANGES if lo <= n <= hi), "")


def norm_state(s: str) -> str:
    s = (s or "").strip().rstrip(".")
    if s.upper() in STATES:
        return s.upper()
    return STATE_BY_NAME.get(s.lower(), "")


def norm_zip(s: str) -> str:
    s = (s or "").strip().upper()
    m = re.search(r"(?<!\d)\d{5}(?!\d)", s)
    if m:
        return m.group(0)
    m = re.search(r"\b([A-Z]\d[A-Z])\s*(\d[A-Z]\d)\b", s)  # Canadian postal code
    return f"{m.group(1)} {m.group(2)}" if m else ""


CITY_WORDS = {"saint": "st", "fort": "ft", "mount": "mt"}


def city_key(s: str) -> str:
    """"St. Louis" and "Saint Louis" -> "st louis"."""
    return " ".join(CITY_WORDS.get(w, w) for w in re.findall(r"[a-z]+", (s or "").lower()))


def city_in(city: str, text: str) -> bool:
    return bool(city_key(city)) and set(city_key(city).split()) <= {CITY_WORDS.get(w, w) for w in words(text)}


def state_in(state: str, text: str) -> bool:
    return bool(re.search(rf"\b{state}\b", text)) or STATES[state].lower() in text.lower()


def zip_in(z: str, text: str) -> bool:
    if " " in z:  # Canadian
        return z.replace(" ", "") in re.sub(r"\s", "", text.upper())
    return bool(re.search(rf"(?<!\d){z}(?!\d)", text))


EQUIPMENT_EVIDENCE = {
    "power_only": r"\bpower[\s-]*only\b",
    "stepdeck": r"\bstep[\s-]*decks?\b|\bdrop[\s-]*decks?\b",
    "conestoga": r"\bconestogas?\b",
    "flatbed": r"\bflat[\s-]*beds?\b",
    "reefer": r"\breefers?\b|\brefrigerated\b|\btemp(?:erature)?[\s-]*controlled\b|\bfrozen\b|\bchilled\b",
    "box_truck": r"\b(?:box|straight)[\s-]*trucks?\b",
    "van": r"\b(?:dry[\s-]*)?vans?\b",
}


def canonical_equipment(s: str) -> str:
    """The model's equipment words as one of the types the pricing knows, or "" if it isn't one."""
    low = (s or "").lower()
    for kind, rx in EQUIPMENT_EVIDENCE.items():
        if re.search(rx, low) or low.replace(" ", "_") == kind:
            return kind
    return ""


HAZMAT_RE = re.compile(r"\bhaz(?:mat|ardous)\b|\bUN\s?\d{4}\b|\bclass\s+[1-9](?:\.\d)?\b|\bplacard(?:s|ed)?\b", re.I)
NEGATED = re.compile(r"(?:\bno|\bnon|\bnot)[\s-]*$", re.I)


def hazmat_mentions(text: str) -> list[str]:
    """Hazmat words in the text, skipping "no hazmat" and "non-hazardous"."""
    return [m.group(0) for m in HAZMAT_RE.finditer(text) if not NEGATED.search(text[max(0, m.start() - 6):m.start()])]


# Special requirements the brokerage charges for, and the words that show the email asked for them
CHARGED = {
    "tarps": r"\btarp(?:s|ped|ing)?\b",
    "team": r"\bteam[\s-]+(?:drivers?|service|required|needed|run|expedite)\b|\b(?:needs?|run|requires?)\s+(?:a\s+)?team\b",
    "liftgate": r"\blift[\s-]*gate\b",
}
PICKUP_WORDS = re.compile(r"\bpick(?:ing|ed)?[\s-]*up\b|\bp/?u\b|\borigin\b|\bshipper\b|\bship(?:s|ping)?\s+from\b|"
                          r"\bload(?:ing|s)?\s+(?:at|in)\b|\bout\s+of\b", re.I)
DELIVERY_WORDS = re.compile(r"\bdeliver(?:y|ing|s|ed)?\b|\bdrop(?:ping)?\b|\bconsignee\b|\bdest(?:ination)?\b|"
                            r"\breceiver\b|\bship\s+to\b|\bgoing\s+to\b", re.I)


# --- checked results ------------------------------------------------------------------------------------------------

@dataclass
class Flag:
    code: str  # stable short name, used by tests and scoring
    text: str


@dataclass
class Shipment:
    email: str = ""
    lane: int = 1  # position in the email
    kind: str = ""
    customer: str = ""
    origin: dict = field(default_factory=dict)  # {"city", "state", "zip"}
    destination: dict = field(default_factory=dict)
    stops: list[dict] = field(default_factory=list)
    pickup_date: str = ""
    pickup_time: str = ""
    delivery_date: str = ""
    delivery_time: str = ""
    equipment: str = ""  # a key of EQUIPMENT_LABELS, or ""
    weight_lbs: float | None = None
    commodity: str = ""
    pieces: str = ""
    temperature: str = ""
    hazmat: bool = False
    requirements: list[str] = field(default_factory=list)
    charged: list[str] = field(default_factory=list)  # keys of the extra charges that apply (tarps, team, ...)
    references: list[str] = field(default_factory=list)
    rate: float | None = None  # a rate the sender gave
    truckloads: int = 1
    missing: list[str] = field(default_factory=list)  # required details the email doesn't give
    flags: list[Flag] = field(default_factory=list)
    quote: object | None = None  # pricing.Quote, set after the checks

    def place_text(self, p: dict) -> str:
        cs = ", ".join(x for x in (p.get("city", ""), p.get("state", "")) if x)
        return " ".join(x for x in (cs, p.get("zip", "")) if x) or "(not given)"

    def lane_text(self) -> str:
        return " -> ".join(self.place_text(p) for p in [self.origin, *self.stops, self.destination])


def place_ok(p: dict) -> bool:
    return bool((p.get("city") and p.get("state")) or p.get("zip"))


def _check_place(label: str, p: Place, text: str, flags: list[Flag]) -> dict:
    city, state, z = p.city.strip(), norm_state(p.state), norm_zip(p.zip)
    m = re.match(r"(.+?),\s*([A-Za-z]{2})$", city)  # "Dallas, TX" in the city field
    if m and norm_state(m.group(2)) and state in ("", norm_state(m.group(2))):
        city, state = m.group(1).strip(), norm_state(m.group(2))
    if p.state and not state:
        flags.append(Flag("not_in_email", f"{label} state '{p.state}' is not a US state or Canadian province"))
    if p.zip and not z:
        flags.append(Flag("not_in_email", f"removed {label} ZIP '{p.zip}' (not a ZIP code)"))
    if z and not zip_in(z, text):
        flags.append(Flag("not_in_email", f"removed {label} ZIP {z} (not in the email)"))
        z = ""
    if city and not city_in(city, text):
        flags.append(Flag("not_in_email", f"{label} city '{city}' is not in the email"))
    zs = zip_state(z)
    if state and not state_in(state, text) and zs != state:
        flags.append(Flag("not_in_email", f"{label} state {state} is not in the email"))
    if state and zs and zs != state:
        flags.append(Flag("zip_state", f"{label} ZIP {z} is in {zs}, not {state}"))
    if not state and zs and city:
        state = zs  # the ZIP says which state
    return {"city": city, "state": state, "zip": z}


def _check_date(label: str, raw: str, text: str, ref: date, flags: list[Flag]) -> str:
    if not raw:
        return ""
    d = parse_date(raw, ref)
    if d is None:
        flags.append(Flag("date", f"removed {label} '{raw}' (not a date)"))
        return ""
    if d not in find_dates(text, ref):
        flags.append(Flag("not_in_email", f"removed {label} {d} (not in the email)"))
        return ""
    return d.isoformat()


def _swap_check(s: Shipment, text: str, flags: list[Flag]) -> None:
    """Flags a lane whose origin only shows up on delivery lines (or the other way round)."""
    o, d = s.origin.get("city", ""), s.destination.get("city", "")
    if not o or not d or city_key(o) == city_key(d):
        return
    lines = text.splitlines()

    def rx(city: str):
        return re.compile(r"\b" + r"\W+".join(map(re.escape, city.split())) + r"\b", re.I)

    ro, rd = rx(o), rx(d)
    for line in lines:
        mo, md = ro.search(line), rd.search(line)
        if mo and md and md.start() < mo.start() and not re.search(r"\bfrom\b|\bout of\b|<-",
                                                                   line[md.end():mo.start()], re.I):
            flags.append(Flag("swap", f"origin and destination may be swapped: '{line.strip()[:80]}'"))
            return
    for city, own, other, rest in ((o, PICKUP_WORDS, DELIVERY_WORDS, rd), (d, DELIVERY_WORDS, PICKUP_WORDS, ro)):
        on = [ln for ln in lines if rx(city).search(ln) and not rest.search(ln)]  # lines naming both: order above
        if on and all(other.search(ln) and not own.search(ln) for ln in on):
            flags.append(Flag("swap", "origin and destination may be swapped: "
                                      f"'{city}' only appears on {'delivery' if own is PICKUP_WORDS else 'pickup'} lines"))
            return


def check_shipment(x: ExtractedShipment, e: Email, kind: str, customer: str, lane: int, cfg: BrokerConfig,
                   ref: date, hazmat_words: list[str], lanes_in_email: int) -> Shipment:
    """Deterministic checks on one lane: values not in the email are removed or flagged, missing details listed."""
    text = e.all_text()
    flags: list[Flag] = []
    s = Shipment(email=e.file, lane=lane, kind=kind, customer=customer, pickup_time=x.pickup_time,
                 delivery_time=x.delivery_time, truckloads=x.truckloads)
    s.origin = _check_place("origin", x.origin, text, flags)
    s.destination = _check_place("destination", x.destination, text, flags)
    s.stops = [_check_place(f"stop {i}", p, text, flags) for i, p in enumerate(x.extra_stops, 1)]
    s.stops = [p for p in s.stops if place_ok(p)]

    s.pickup_date = _check_date("pickup date", x.pickup_date, text, ref, flags)
    s.delivery_date = _check_date("delivery date", x.delivery_date, text, ref, flags)
    if s.pickup_date and e.day and s.pickup_date < e.day.isoformat():
        flags.append(Flag("date", f"pickup date {s.pickup_date} is before the email was sent ({e.day})"))
    if s.pickup_date and s.delivery_date and s.delivery_date < s.pickup_date:
        flags.append(Flag("date", f"delivery {s.delivery_date} is before pickup {s.pickup_date}"))

    eq = canonical_equipment(x.equipment)
    if x.equipment and not eq:
        flags.append(Flag("equipment", f"equipment '{x.equipment}' is not a type the pricing knows"))
    elif eq and not re.search(EQUIPMENT_EVIDENCE[eq], text, re.I):
        flags.append(Flag("equipment", f"removed equipment '{x.equipment}' (the email doesn't say it)"))
        eq = ""
    s.equipment = eq

    if x.weight_lbs is not None:
        if has_amount(x.weight_lbs, text):
            s.weight_lbs = x.weight_lbs
        else:
            flags.append(Flag("not_in_email", f"removed weight {x.weight_lbs:,.0f} lbs (not in the email)"))
    for label, value in (("pieces", x.pieces), ("temperature", x.temperature)):
        nums = [float(n.replace(",", "")) for n in NUM_RE.findall(value)]
        if nums and not all(has_amount(n, text) for n in nums):
            flags.append(Flag("not_in_email", f"removed {label} '{value}' (not in the email)"))
            value = ""
        setattr(s, label, value)
    s.commodity = x.commodity
    if x.commodity and not {w for w in words(x.commodity) if len(w) > 2} & words(text):
        flags.append(Flag("not_in_email", f"commodity '{x.commodity}' is not in the email"))

    for r in x.references:
        core = [t for t in re.findall(r"[A-Za-z0-9-]+", r) if re.search(r"\d", t) and len(t) >= 3]
        if core and all(re.search(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", text, re.I) for t in core):
            s.references.append(r)
        else:
            flags.append(Flag("not_in_email", f"removed reference '{r}' (not in the email)"))

    if x.rate is not None:
        if has_amount(x.rate, text):
            s.rate = x.rate
        else:
            flags.append(Flag("not_in_email", f"removed rate ${x.rate:,.2f} (not in the email)"))
    if s.truckloads > 1 and not has_amount(s.truckloads, text):
        flags.append(Flag("not_in_email", f"{s.truckloads} truckloads is not in the email"))

    for r in x.requirements:
        key = next((k for k, rx in CHARGED.items() if re.search(rx, r, re.I) or r.strip().lower() == k), "")
        if key and not re.search(CHARGED[key], text, re.I):
            flags.append(Flag("not_in_email", f"removed requirement '{r}' (not in the email)"))
            continue
        if not key and not {w for w in words(r) if len(w) > 2} & words(text):
            flags.append(Flag("not_in_email", f"removed requirement '{r}' (not in the email)"))
            continue
        s.requirements.append(r)
        if key and key not in s.charged:
            s.charged.append(key)

    # hazmat is decided by the email's words; the model only has to say which lane it is on
    if hazmat_words:
        if x.hazmat or lanes_in_email == 1:
            s.hazmat = True
            if not x.hazmat:
                flags.append(Flag("hazmat", f"the email mentions hazmat ({hazmat_words[0]}) but the model missed it"))
            said: dict[str, str] = {}
            for w in hazmat_words:
                said.setdefault(w.lower(), w)
            flags.append(Flag("hazmat", f"hazmat ({', '.join(said.values())}): confirm UN number, class "
                                        "and placards, and use a carrier with hazmat authority"))
        else:
            flags.append(Flag("hazmat", f"the email mentions hazmat ({hazmat_words[0]}); check whether this lane is"))
    elif x.hazmat:
        flags.append(Flag("not_in_email", "removed hazmat (the email doesn't say the freight is hazardous)"))
    if s.hazmat:
        s.charged.append("hazmat")
    if s.stops:
        s.charged.append("extra_stop")

    limit = cfg.max_weight_lbs.get(s.equipment)
    if s.weight_lbs and limit and s.weight_lbs > limit:
        flags.append(Flag("overweight", f"{s.weight_lbs:,.0f} lbs is over the usual {limit:,.0f} lbs for a "
                                        f"{s.equipment.replace('_', ' ')}: confirm the weight, or it needs permits"))
    pallets = re.search(r"(\d+)\s*(?:pallets?|skids?|plts?)\b", s.pieces, re.I)
    light = s.weight_lbs is not None and s.weight_lbs <= cfg.small_shipment_lbs
    if light or (pallets and int(pallets.group(1)) < cfg.small_shipment_pallets):
        flags.append(Flag("small_shipment", "LTL-size shipment: price it by hand or send it to an LTL carrier"))
    _swap_check(s, text, flags)

    present = {"origin": place_ok(s.origin), "destination": place_ok(s.destination), "equipment": bool(s.equipment),
               "pickup_date": bool(s.pickup_date), "delivery_date": bool(s.delivery_date),
               "weight": s.weight_lbs is not None, "commodity": bool(s.commodity), "temperature": bool(s.temperature)}
    required = list(cfg.required) + (["temperature"] if s.equipment == "reefer" else [])
    s.missing = [f for f in dict.fromkeys(required) if not present[f]]
    if kind == "tender" and not s.references:
        flags.append(Flag("reference", "tender without a load or PO number"))
    s.flags = list({(f.code, f.text): f for f in flags}.values())
    return s


@dataclass
class EmailResult:
    email: Email
    kind: str = "other"
    customer: str = ""
    contact: str = ""
    shipments: list[Shipment] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)  # about the email as a whole
    error: str = ""  # the model request failed; re-run to retry
    reply_file: str = ""


def check_reading(r: EmailReading, e: Email, cfg: BrokerConfig, today: date | None = None) -> EmailResult:
    out = EmailResult(email=e, kind=r.kind, customer=r.customer, contact=r.contact)
    ref = e.day or today or date.today()
    if not e.day:
        out.flags.append(Flag("date", "the email has no sent date; words like 'tomorrow' were read from today"))
    for name in e.skipped:
        out.flags.append(Flag("attachment", f"attachment '{name}' could not be read; open it"))
    if r.kind == "other":
        if r.shipments:
            out.flags.append(Flag("kind", "read as not a request, but it describes a shipment; check it"))
        return out
    if not r.shipments:
        out.flags.append(Flag("kind", f"read as a {r.kind.replace('_', ' ')} but no lane was found"))
    text = e.all_text()
    hz = hazmat_mentions(text)
    out.shipments = [check_shipment(x, e, r.kind, r.customer, i, cfg, ref, hz, len(r.shipments))
                     for i, x in enumerate(r.shipments, 1)]
    return out
