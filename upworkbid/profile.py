"""The freelancer's profile (TOML file): rate floor, skills, portfolio, and hours per kind of project."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

BUCKETS = ["less than 5", "5 to 10", "10 to 15", "15 to 20", "20 to 50", "50+"]
DEFAULT_BANNED = ["dear hiring manager", "dear sir", "i am the perfect fit", "i am the best fit", "i have read your job",
                  "i read your job description", "hope this message finds you well", "hope you are doing well",
                  "kindly", "100% satisfaction", "guarantee", "i am an expert in everything"]


@dataclass
class PortfolioItem:
    id: str
    title: str
    summary: str
    link: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class ProjectType:
    name: str
    hours: tuple[float, float]


@dataclass
class Profile:
    name: str = ""
    headline: str = ""
    hourly_rate: float = 100.0
    floor_after_fees: float = 90.0  # the kill rule in analysis/model.py: offers must work out at $90/h or more
    fee: float = 0.10  # Upwork's freelancer fee
    min_fixed_budget: float = 500.0
    max_proposals: str = "20 to 50"  # skip jobs that already have more proposals than this
    skills: list[str] = field(default_factory=list)
    exclude_words: list[str] = field(default_factory=list)
    proposal_words: int = 150
    banned: list[str] = field(default_factory=lambda: list(DEFAULT_BANNED))
    signature: str = ""
    portfolio: list[PortfolioItem] = field(default_factory=list)
    project_types: list[ProjectType] = field(default_factory=list)

    def item(self, pid: str) -> PortfolioItem | None:
        return next((p for p in self.portfolio if p.id.lower() == (pid or "").strip().lower()), None)

    def project_type(self, name: str) -> ProjectType | None:
        return next((t for t in self.project_types if t.name.lower() == (name or "").strip().lower()), None)

    def claims_text(self) -> str:
        """Everything a proposal may claim about the freelancer; numbers in it must come from here or the job."""
        return "\n".join([self.name, self.headline, f"${self.hourly_rate:,.0f}/h"]
                         + [f"{p.id} {p.title}: {p.summary} {p.link}" for p in self.portfolio])

    def as_text(self) -> str:
        lines = [f"Freelancer: {self.name}", f"Headline: {self.headline}", f"Skills: {', '.join(self.skills)}",
                 f"Rate: ${self.hourly_rate:,.0f}/h", "Portfolio:"]
        lines += [f"[{p.id}] {p.title}: {p.summary}" + (f" ({p.link})" if p.link else "") for p in self.portfolio]
        lines.append("Project types: " + "; ".join(t.name for t in self.project_types))
        return "\n".join(lines)


def load_profile(path: str | Path) -> Profile:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    p = Profile()
    info = data.get("profile") or {}
    unknown = sorted(set(info) - (set(Profile.__dataclass_fields__) - {"portfolio", "project_types"}))
    if unknown:
        raise ValueError(f"{path}: unknown [profile] settings: {', '.join(unknown)}")
    for k, v in info.items():
        setattr(p, k, v)
    for x in data.get("portfolio") or []:
        if not x.get("id") or not x.get("title") or not x.get("summary"):
            raise ValueError(f"{path}: each [[portfolio]] needs id, title and summary")
        p.portfolio.append(PortfolioItem(x["id"], x["title"], x["summary"], x.get("link", ""), x.get("tags", [])))
    for t in data.get("project_type") or []:
        hours = t.get("hours") or []
        if not t.get("name") or len(hours) != 2 or hours[0] > hours[1]:
            raise ValueError(f"{path}: each [[project_type]] needs a name and hours = [low, high]")
        p.project_types.append(ProjectType(t["name"], (float(hours[0]), float(hours[1]))))
    p.skills = [s.lower() for s in p.skills]
    p.banned = [b.lower() for b in p.banned]
    if p.max_proposals.lower() not in BUCKETS:
        raise ValueError(f"{path}: max_proposals must be one of {BUCKETS}")
    if not p.name or not p.portfolio or not p.project_types:
        raise ValueError(f"{path}: needs [profile] name, at least one [[portfolio]] item and one [[project_type]]")
    return p
