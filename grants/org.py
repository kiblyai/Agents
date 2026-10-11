"""The nonprofit's profile (TOML file): where it works, what it does, its programs and the grant sizes it seeks."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from agentkit.places import norm_state


@dataclass
class Program:
    name: str
    summary: str = ""
    budget: float = 0.0


@dataclass
class Org:
    name: str = ""
    mission: str = ""
    city: str = ""
    state: str = ""
    service_area: list[str] = field(default_factory=list)  # counties or cities served, e.g. ["Millbrook County"]
    annual_budget: float = 0.0
    founded: int = 0
    ask_min: float = 5000.0
    ask_max: float = 50000.0
    focus: list[str] = field(default_factory=list)
    populations: list[str] = field(default_factory=list)
    programs: list[Program] = field(default_factory=list)

    def program(self, name: str) -> Program | None:
        key = (name or "").strip().lower()
        return next((p for p in self.programs if p.name.lower() == key), None)

    def as_text(self) -> str:
        """What the model is told about the nonprofit; numbers in a draft may also come from here."""
        lines = [f"Name: {self.name}", f"Mission: {self.mission}", f"Based in: {self.city}, {self.state}",
                 f"Serves: {', '.join(self.service_area) or self.state}",
                 f"Annual budget: ${self.annual_budget:,.0f}"]
        if self.founded:
            lines.append(f"Founded: {self.founded}")
        if self.focus:
            lines.append(f"Focus: {', '.join(self.focus)}")
        if self.populations:
            lines.append(f"Serves: {', '.join(self.populations)}")
        lines.append(f"Grant sizes sought: ${self.ask_min:,.0f} to ${self.ask_max:,.0f}")
        for p in self.programs:
            lines.append(f"Program: {p.name}: {p.summary}" + (f" (budget ${p.budget:,.0f})" if p.budget else ""))
        return "\n".join(lines)


def load_org(path: str | Path) -> Org:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    o = Org()
    info = data.get("org") or {}
    unknown = sorted(set(info) - (set(Org.__dataclass_fields__) - {"programs"}))
    if unknown:
        raise ValueError(f"{path}: unknown [org] settings: {', '.join(unknown)}")
    for k, v in info.items():
        setattr(o, k, v)
    for p in data.get("program") or []:
        extra = sorted(set(p) - {"name", "summary", "budget"})
        if extra or not p.get("name"):
            raise ValueError(f"{path}: each [[program]] needs a name (and may have summary, budget); got {sorted(p)}")
        o.programs.append(Program(name=p["name"], summary=p.get("summary", ""), budget=float(p.get("budget", 0))))
    problems = []
    if not o.name or not o.mission:
        problems.append("[org] needs name and mission")
    if not norm_state(o.state):
        problems.append("[org] state must be a US state, e.g. \"OH\"")
    if not o.programs:
        problems.append("add at least one [[program]] with a name and summary")
    if o.ask_min > o.ask_max:
        problems.append("[org] ask_min is larger than ask_max")
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    o.state = norm_state(o.state)
    return o
