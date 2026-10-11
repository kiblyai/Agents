"""Reads HubSpot exports (contacts and companies) into records keyed by HubSpot's internal property names."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentkit.tables import norm_header, read_table

# Export column labels -> HubSpot internal property names (what the API and imports use).
CONTACT_PROPS = {
    "record id": "hs_object_id", "contact id": "hs_object_id", "first name": "firstname", "last name": "lastname",
    "email": "email", "phone number": "phone", "phone": "phone", "mobile phone number": "mobilephone",
    "job title": "jobtitle", "company name": "company", "company": "company", "lifecycle stage": "lifecyclestage",
    "last activity date": "notes_last_updated", "create date": "createdate", "contact owner": "hubspot_owner_id",
    "associated company id": "associatedcompanyid", "associated company": "associated_company",
    "primary associated company id": "associatedcompanyid", "city": "city", "state/region": "state",
    "country/region": "country", "website url": "website",
}
COMPANY_PROPS = {
    "record id": "hs_object_id", "company id": "hs_object_id", "company name": "name", "name": "name",
    "company domain name": "domain", "domain": "domain", "website url": "website", "industry": "industry",
    "number of employees": "numberofemployees", "city": "city", "state/region": "state", "country/region": "country",
    "phone number": "phone", "description": "description", "last activity date": "notes_last_updated",
    "create date": "createdate", "annual revenue": "annualrevenue",
}


@dataclass
class Record:
    kind: str  # "contact" or "company"
    id: str
    props: dict[str, str] = field(default_factory=dict)
    row: int = 0

    def get(self, name: str) -> str:
        return (self.props.get(name) or "").strip()

    @property
    def label(self) -> str:
        if self.kind == "company":
            return self.get("name") or self.get("domain") or f"company {self.id}"
        name = " ".join(x for x in (self.get("firstname"), self.get("lastname")) if x)
        return name or self.get("email") or f"contact {self.id}"


def read_export(path: str | Path, kind: str) -> tuple[list[Record], list[str]]:
    """Records from a HubSpot export (or any CSV/Excel using the same column names), and the columns not used."""
    mapping = CONTACT_PROPS if kind == "contact" else COMPANY_PROPS
    rows = read_table(path, strip=False)  # spaces around values are themselves something to fix
    unknown = sorted({k for r in rows[:1] for k in r if norm_header(k) not in mapping})
    out = []
    for i, r in enumerate(rows, 2):
        props = {}
        for k, v in r.items():
            name = mapping.get(norm_header(k))
            if name and (v or name not in props):
                props[name] = v
        rid = props.pop("hs_object_id", "").strip() or f"row{i}"
        out.append(Record(kind, rid, props, i))
    if rows and not any(r.id and not r.id.startswith("row") for r in out):
        raise ValueError(f"{Path(path).name}: no 'Record ID' column. Export from HubSpot (Contacts > Export) so changes "
                         "can be matched back to the records")
    return out, unknown
