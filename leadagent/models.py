"""Data shapes shared across the pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Page(BaseModel):
    url: str
    title: str = ""
    text: str = ""


class Signal(BaseModel):
    """A reason to reach out now, tied to the page it came from."""

    type: str = ""
    evidence: str = ""
    source_url: str = ""
    date: str | None = None
    verified: bool = False  # set by us, not the model: evidence found on the cited page


class CompanyResearch(BaseModel):
    company_name: str = ""
    summary: str = ""
    fits_icp: bool = False
    score: int = 0
    score_reasons: list[str] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    why_now: str = ""
    disqualifiers: list[str] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def _clamp_score(cls, v):
        try:
            v = round(float(v))
        except (TypeError, ValueError):
            return 0
        return max(0, min(10, int(v)))

    @field_validator("signals", mode="before")
    @classmethod
    def _coerce_signals(cls, v):
        if not isinstance(v, list):
            return []
        return [{"evidence": s} if isinstance(s, str) else s for s in v]

    @field_validator("score_reasons", "disqualifiers", mode="before")
    @classmethod
    def _coerce_str_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return v


class FirstLine(BaseModel):
    line: str = ""


class Contact(BaseModel):
    domain: str
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    email: str = ""
    email_status: str = ""

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p)


class CompanyResult(BaseModel):
    domain: str
    input_name: str = ""
    status: str = ""  # qualified | not_fit | unreachable | error
    error: str = ""
    pages: list[str] = Field(default_factory=list)
    research: CompanyResearch | None = None
    first_line: str = ""
    first_line_status: str = ""  # ok | needs_manual | skipped
    icp_hash: str = ""
