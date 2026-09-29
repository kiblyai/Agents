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
    headline: str = ""  # plain-English version, blanked by us if the source doesn't support it
    evidence: str = ""
    source_url: str = ""
    date: str | None = None
    verified: bool = False  # set by us, not the model: evidence found on the cited page
    stale: bool = False  # set by us: dated more than 12 months ago

    @field_validator("type", "headline", "evidence", "source_url", mode="before")
    @classmethod
    def _as_str(cls, v):
        return "" if v is None else str(v)

    @field_validator("date", mode="before")
    @classmethod
    def _date_as_str(cls, v):
        if v in (None, "", "null", "None"):
            return None
        return str(v)


class Criterion(BaseModel):
    """One line of the target-customer profile, judged against the evidence."""

    name: str = ""
    status: str = "unknown"  # met | not_met | unknown
    evidence: str = ""

    @field_validator("name", "evidence", mode="before")
    @classmethod
    def _as_str(cls, v):
        return "" if v is None else str(v)

    @field_validator("status", mode="before")
    @classmethod
    def _norm_status(cls, v):
        s = str(v).strip().lower().replace("-", "_").replace(" ", "_")
        if s in {"met", "yes", "true", "pass", "fit"}:
            return "met"
        if s in {"not_met", "no", "false", "fail", "unmet", "not_fit"}:
            return "not_met"
        return "unknown"


class CompanyResearch(BaseModel):
    company_name: str = ""
    summary: str = ""
    fits_icp: bool = False
    score: int = 0
    score_reasons: list[str] = Field(default_factory=list)
    criteria: list[Criterion] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    why_now_index: int | None = None
    why_now: str = ""  # rebuilt by us from the chosen verified, current signal
    disqualifiers: list[str] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def _clamp_score(cls, v):
        try:
            v = round(float(v))
        except (TypeError, ValueError):
            return 0
        return max(0, min(10, int(v)))

    @field_validator("why_now_index", mode="before")
    @classmethod
    def _index(cls, v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    @field_validator("signals", "criteria", mode="before")
    @classmethod
    def _coerce_items(cls, v):
        if not isinstance(v, list):
            return []
        return [{"evidence": s} if isinstance(s, str) else s for s in v if isinstance(s, (str, dict, BaseModel))]

    @field_validator("score_reasons", "disqualifiers", mode="before")
    @classmethod
    def _coerce_str_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return [str(x) for x in v]


class FirstLine(BaseModel):
    line: str = ""

    @field_validator("line", mode="before")
    @classmethod
    def _as_str(cls, v):
        return "" if v is None else str(v)


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
