"""A client's campaign (TOML file): who sends, what they offer, the claims emails may make, and the meeting bar."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BANNED = ["hope this email finds you well", "hope you're well", "hope you are well", "synergy",
                  "touch base", "circle back", "game-changer", "game changer", "revolutionize", "cutting-edge",
                  "world-class", "best-in-class", "guarantee", "guaranteed", "just following up", "quick question",
                  "i wanted to reach out", "per my last email"]
DEFAULT_FOOTER = ("{sender_name}\n{sender_title}, {client}\n{postal_address}\n"
                  "Not the right time? Reply \"no thanks\" and I won't email again.")


@dataclass
class Campaign:
    client: str = ""
    website: str = ""
    sending_domains: list[str] = field(default_factory=list)  # domains the emails go out from (often lookalikes)
    sender_name: str = ""
    sender_title: str = ""
    postal_address: str = ""  # US anti-spam law (CAN-SPAM) needs it in every email
    booking_link: str = ""
    offer: str = ""
    proof: list[str] = field(default_factory=list)  # the only results and claims emails may state
    cta: str = "Worth a 20-minute call next week?"
    footer: str = DEFAULT_FOOTER
    # sequence
    days: list[int] = field(default_factory=lambda: [0, 3, 8])  # sending day of each email
    max_words: list[int] = field(default_factory=lambda: [110, 60, 50])
    max_subject_words: int = 6
    links_in_first_email: bool = False  # links in a first cold email hurt deliverability
    banned: list[str] = field(default_factory=lambda: list(DEFAULT_BANNED))
    reply_max_words: int = 90
    # meeting bar: who counts as a billable meeting
    titles: list[str] = field(default_factory=list)  # words in the job title, e.g. "owner", "coo"
    min_employees: int = 0
    max_employees: int = 0  # 0 = no upper limit
    countries: list[str] = field(default_factory=list)  # e.g. ["US"]; empty = anywhere
    write_outside_bar: bool = False  # also write emails to leads that would not be billable
    # billing
    platform_fee: float = 500.0
    per_meeting: float = 250.0

    @property
    def steps(self) -> int:
        return len(self.days)

    def footer_text(self) -> str:
        return self.footer.format(sender_name=self.sender_name, sender_title=self.sender_title, client=self.client,
                                  postal_address=self.postal_address).strip()

    def proof_block(self) -> str:
        return "\n".join(f"[P{i}] {p}" for i, p in enumerate(self.proof, 1))

    def claims_text(self) -> str:
        """Everything an email may state about the client; numbers in a draft must come from here or the lead."""
        return "\n".join([self.client, self.website, self.offer, *self.proof, self.cta, self.booking_link,
                          self.sender_name, self.sender_title])


SECTIONS = {"client": {"client", "website", "sending_domains", "sender_name", "sender_title", "postal_address", "booking_link", "offer",
                       "proof", "cta", "footer"},
            "sequence": {"days", "max_words", "max_subject_words", "links_in_first_email", "banned", "reply_max_words"},
            "bar": {"titles", "min_employees", "max_employees", "countries", "write_outside_bar"},
            "billing": {"platform_fee", "per_meeting"}}
RENAMED = {"client": {"name": "client"}}


def load_campaign(path: str | Path) -> Campaign:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    c = Campaign()
    for section, allowed in SECTIONS.items():
        values = dict(data.get(section) or {})
        for old, new in RENAMED.get(section, {}).items():
            if old in values:
                values[new] = values.pop(old)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"{path}: unknown [{section}] settings: {', '.join(unknown)}")
        for k, v in values.items():
            setattr(c, k, v)
    extra = sorted(set(data) - set(SECTIONS))
    if extra:
        raise ValueError(f"{path}: unknown sections: {', '.join(extra)} (use {', '.join(SECTIONS)})")
    problems = []
    if not c.client or not c.sender_name:
        problems.append("[client] needs name and sender_name")
    if not c.postal_address:
        problems.append("[client] needs postal_address: US law (CAN-SPAM) requires it in every cold email")
    if not c.offer:
        problems.append("[client] needs offer: one or two sentences on what the client sells")
    if len(c.max_words) != len(c.days):
        problems.append("[sequence] max_words needs one limit per email in days")
    if c.days != sorted(c.days) or not c.days:
        problems.append("[sequence] days must list each email's sending day in order, e.g. [0, 3, 8]")
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    c.banned = [b.lower() for b in c.banned]
    c.sending_domains = [d.lower().strip() for d in c.sending_domains] or ([c.website.lower()] if c.website else [])
    c.countries = [x.upper() for x in c.countries]
    c.titles = [t.lower() for t in c.titles]
    return c
