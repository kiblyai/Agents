"""The cleanup rules, all in code: duplicates, formats, associations and stale records. No model involved."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from agentkit.checks import parse_date, valid_email
from agentkit.places import STATES, norm_state

from .config import CRMConfig
from .records import Record

FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
             "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com", "yandex.com",
             "comcast.net", "att.net", "verizon.net"}
COUNTRY_ALIASES = {"us", "usa", "u.s.", "u.s.a.", "united states", "united states of america", "america"}
UPPER_WORDS = {"LLC", "LLP", "LP", "PLLC", "PC", "PA", "USA", "US", "IT", "HR", "AI", "CPA", "CPAS", "MD", "DDS",
               "II", "III", "IV", "NA", "HVAC", "ATM", "B2B", "SAAS"}
TITLE_WORDS = {"INC": "Inc", "CORP": "Corp", "CO": "Co", "LTD": "Ltd", "AND": "and", "OF": "of", "THE": "The",
               "&": "&"}


@dataclass
class Change:
    kind: str
    id: str
    label: str
    field: str
    old: str
    new: str
    reason: str
    by: str = "rule"  # "rule" or "model"
    source: str = ""  # for model changes: the page and the quote that back it


@dataclass
class Merge:
    kind: str
    primary: str
    duplicate: str
    primary_label: str
    duplicate_label: str
    reason: str


@dataclass
class Flag:
    kind: str
    id: str
    label: str
    code: str
    text: str


def norm_domain(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"^[a-z]+://", "", s)
    s = s.split("/")[0].split("?")[0].split("#")[0]
    return s[4:] if s.startswith("www.") else s


def proper_name(s: str) -> str:
    """'JANE' or 'jane' -> 'Jane'; "o'BRIEN" -> "O'Brien"; 'mary-kate' -> 'Mary-Kate'; 'McDonald' unchanged."""
    def fix(word: str) -> str:
        w = word.lower()
        w = re.sub(r"(^|[-'])([a-z])", lambda m: m.group(1) + m.group(2).upper(), w)
        return re.sub(r"^Mc([a-z])", lambda m: "Mc" + m.group(1).upper(), w)
    return " ".join(fix(p) for p in s.split())


def needs_casing(s: str) -> bool:
    letters = re.sub(r"[^A-Za-z]", "", s or "")
    return len(letters) > 1 and (letters.isupper() or letters.islower())


def company_name_case(s: str) -> str:
    words = []
    for w in s.split():
        core = re.sub(r"[^A-Za-z0-9&]", "", w).upper()
        if core in UPPER_WORDS:
            words.append(w.upper())
        elif core in TITLE_WORDS:
            words.append(w.replace(core, TITLE_WORDS[core]) if core != "&" else w)
        else:
            words.append(w[:1].upper() + w[1:].lower())
    out = " ".join(words)
    return out[:1].upper() + out[1:]


def us_phone(s: str) -> tuple[str, bool]:
    """('+1 555-201-3344', True) for a US number in any format; (s, False) when it can't be a phone number."""
    raw = (s or "").strip()
    ext = ""
    m = re.search(r"\s*(?:x|ext\.?|extension)\s*(\d+)\s*$", raw, re.I)
    if m:
        ext, raw = f" ext. {m.group(1)}", raw[:m.start()]
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and not raw.startswith("+1"):
        return s, len(digits) >= 8  # an international number: leave it alone
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return s, False
    return f"+1 {digits[:3]}-{digits[3:6]}-{digits[6:]}{ext}", True


def _filled(r: Record) -> int:
    return sum(1 for v in r.props.values() if v)


def _activity(r: Record) -> date:
    return parse_date(r.get("notes_last_updated")[:10]) or date.min


def _pick_primary(group: list[Record]) -> Record:
    """The record to keep: most recent activity, then the most filled-in, then the oldest id."""
    return max(group, key=lambda r: (_activity(r), _filled(r), -int(re.sub(r"\D", "", r.id) or 0)))


def _merges(kind: str, groups: dict[str, list[Record]], reason: str) -> list[Merge]:
    out = []
    for key, group in groups.items():
        if len(group) < 2:
            continue
        keep = _pick_primary(group)
        for r in group:
            if r is not keep:
                out.append(Merge(kind, keep.id, r.id, keep.label, r.label, reason.format(key=key)))
    return out


def company_rules(companies: list[Record], cfg: CRMConfig) -> tuple[list[Change], list[Merge], list[Flag]]:
    changes, flags = [], []
    by_domain: dict[str, list[Record]] = {}
    by_name: dict[str, list[Record]] = {}
    for c in companies:
        def change(field, new, reason):
            changes.append(Change("company", c.id, c.label, field, c.get(field), new, reason))
        domain = norm_domain(c.get("domain") or c.get("website"))
        if domain and domain != c.get("domain"):
            change("domain", domain, "domain standardized (no https://, www or path)")
        if domain:
            by_domain.setdefault(domain, []).append(c)
        name = c.get("name")
        if name and re.sub(r"[^A-Za-z]", "", name).isupper() and len(name) > 4:
            fixed = company_name_case(name)
            if fixed != name:
                change("name", fixed, "name was in capitals")
        key = re.sub(r"\b(inc|llc|ltd|co|corp|corporation|company|the)\b|[^a-z0-9]", "", (name or "").lower())
        if key:
            by_name.setdefault(key, []).append(c)
        _place_rules(c, cfg, change)
        phone = c.get("phone")
        if phone:
            fixed, ok = us_phone(phone)
            if not ok:
                flags.append(Flag("company", c.id, c.label, "invalid_phone", f"phone '{phone}' doesn't look like a number"))
            elif fixed != phone:
                change("phone", fixed, "phone standardized")
    merges = _merges("company", by_domain, "same domain ({key})")
    merged = {m.duplicate for m in merges} | {m.primary for m in merges}
    for key, group in by_name.items():
        rest = [c for c in group if c.id not in merged]
        domains = {norm_domain(c.get("domain")) for c in rest}
        if len(rest) > 1 and (len(domains) > 1 or domains == {""}):  # same name, different or missing domains
            for c in rest:
                flags.append(Flag("company", c.id, c.label, "possible_duplicate",
                                  f"same name as {', '.join(x.label + ' (' + x.id + ')' for x in rest if x is not c)} "
                                  "but a different or missing domain: merge by hand if it's the same company"))
    return changes, merges, flags


def _place_rules(r: Record, cfg: CRMConfig, change) -> None:
    country = r.get("country")
    us = not country or country.strip().lower() in COUNTRY_ALIASES
    if country and country.strip().lower() in COUNTRY_ALIASES and country != cfg.country_name:
        change("country", cfg.country_name, "country standardized")
    state = r.get("state")
    if state and us:
        code = norm_state(state)
        if code:
            want = code if cfg.state_format == "code" else STATES[code]
            if want != state:
                change("state", want, f"state written as {'a code' if cfg.state_format == 'code' else 'a full name'}")


def contact_rules(contacts: list[Record], companies: list[Record], cfg: CRMConfig, today: date
                  ) -> tuple[list[Change], list[Merge], list[Flag]]:
    changes, flags = [], []
    free = FREE_MAIL | set(cfg.extra_free_domains)
    company_by_domain: dict[str, Record] = {}
    for c in companies:
        d = norm_domain(c.get("domain") or c.get("website"))
        if d and d not in company_by_domain:
            company_by_domain[d] = c
    by_email: dict[str, list[Record]] = {}
    by_name_domain: dict[tuple, list[Record]] = {}
    for p in contacts:
        def change(field, new, reason):
            changes.append(Change("contact", p.id, p.label, field, p.props.get(field) or "", new, reason))

        def flag(code, text):
            flags.append(Flag("contact", p.id, p.label, code, text))
        raw = p.props.get("email") or ""
        email = raw.strip().lower()
        if email and email != raw:
            change("email", email, "email had spaces or capitals")
        if email and not valid_email(email):
            flag("invalid_email", f"email '{raw.strip()}' is not a valid address")
        elif email:
            by_email.setdefault(email, []).append(p)
            local, domain = email.split("@")
            if local in cfg.role_prefixes:
                flag("role_address", f"{email} is a shared inbox, not a person; don't add it to sequences")
            if domain not in free:
                key = (p.get("firstname").lower(), p.get("lastname").lower(), domain)
                if key[0] and key[1]:
                    by_name_domain.setdefault(key, []).append(p)
                company = company_by_domain.get(domain)
                if company and not p.get("associatedcompanyid") and not p.get("associated_company"):
                    change("associatedcompanyid", company.id, f"email domain {domain} matches {company.label}")
                elif not company:
                    flag("no_company", f"no company record for {domain}: create one to keep accounts together")
        for field in ("firstname", "lastname"):
            v = p.get(field)
            if needs_casing(v):
                change(field, proper_name(v), "name was all capitals or all lower case")
        for field in ("phone", "mobilephone"):
            v = p.get(field)
            if v:
                fixed, ok = us_phone(v)
                if not ok:
                    flag("invalid_phone", f"{field} '{v}' doesn't look like a phone number")
                elif fixed != v:
                    change(field, fixed, "phone standardized")
        _place_rules(p, cfg, change)
        last = parse_date(p.get("notes_last_updated")[:10])
        stage = p.get("lifecyclestage").lower()
        if last and (today - last).days > cfg.stale_months * 30.4 and stage in ("", "lead", "subscriber"):
            flag("stale", f"no activity since {last.isoformat()} and still a {stage or 'contact with no stage'}: "
                          "re-engage, archive or delete")
    merges = _merges("contact", by_email, "same email ({key})")
    for key, group in by_name_domain.items():
        emails = {g.get("email").lower() for g in group}
        if len(group) > 1 and len(emails) > 1:
            for p in group:
                others = [g for g in group if g is not p]
                flags.append(Flag("contact", p.id, p.label, "possible_duplicate",
                                  f"same name and company as {', '.join(o.get('email') for o in others)}: merge by hand "
                                  "if it's the same person"))
    return changes, merges, flags
