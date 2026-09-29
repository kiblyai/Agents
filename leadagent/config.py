"""Target-customer spec (TOML file) and runtime settings (environment variables)."""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_BANNED_PHRASES = [
    "hope this email finds you",
    "hope you're well",
    "hope you are well",
    "i came across",
    "i noticed",
    "reaching out",
    "quick question",
    "touch base",
    "circle back",
    "synergy",
    "game-changer",
    "game changer",
    "revolutionize",
    "cutting-edge",
    "impressive",
    "amazing",
    "love what you",
    "leverage",
]


@dataclass
class ICP:
    """Who the client wants to sell to, and what makes a company worth contacting now."""

    name: str
    description: str
    industries: list[str] = field(default_factory=list)
    employee_range: list[int] = field(default_factory=list)
    geographies: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)  # funding stages, e.g. ["Seed", "Series A"]
    target_titles: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    min_score: int = 6
    # Which signal types make the best "why now", strongest first.
    signal_priority: list[str] = field(default_factory=lambda: [
        "hiring", "funding", "leadership", "expansion", "launch", "product_update", "other"])

    def fingerprint(self) -> str:
        """Changes whenever the spec changes, so cached results from an older spec are ignored."""
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:12]

    def as_prompt(self) -> str:
        lines = [f"Name: {self.name}", f"Description: {self.description}"]
        if self.industries:
            lines.append("Industries: " + ", ".join(self.industries))
        if len(self.employee_range) == 2:
            lines.append(f"Employees: {self.employee_range[0]}-{self.employee_range[1]}")
        if self.stages:
            lines.append("Funding stage: " + ", ".join(self.stages))
        if self.geographies:
            lines.append("Geographies: " + ", ".join(self.geographies))
        if self.signals:
            lines.append("Buying signals to look for: " + "; ".join(self.signals))
        if self.exclude:
            lines.append("Exclude: " + "; ".join(self.exclude))
        return "\n".join(lines)


@dataclass
class WriterConfig:
    offer: str = ""  # what the sender sells; keeps the line relevant without pitching
    min_words: int = 6
    max_words: int = 25
    banned_phrases: list[str] = field(default_factory=lambda: list(DEFAULT_BANNED_PHRASES))
    tone: str = "plain and specific; no flattery, no hype, no exclamation marks"


def load_spec(path: str | Path) -> tuple[ICP, WriterConfig]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    icp_data = data.get("icp") or {}
    for required in ("name", "description"):
        if not icp_data.get(required):
            raise ValueError(f"{path}: [icp] needs a non-empty '{required}'")
    icp = ICP(**icp_data)
    writer_data = data.get("writer") or {}
    writer = WriterConfig(**writer_data)
    if "banned_phrases" in writer_data and writer_data.get("extend_default_banned", True):
        writer.banned_phrases = sorted(set(DEFAULT_BANNED_PHRASES) | set(writer.banned_phrases))
    return icp, writer


def load_dotenv(path: str | Path = ".env") -> list[str]:
    """Load KEY=VALUE lines from a local .env file into the environment.

    Variables already set in the environment win, so keys set in your shell or hosting settings are never
    overridden. Returns the names loaded (never the values). The .env file is git-ignored.
    """
    path = Path(path)
    if not path.is_file():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    api_key: str = field(default="", repr=False)  # read from OPENROUTER_API_KEY only; never printed
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "openrouter/free"  # routes to an available free model; set a paid model for client work
    writer_model: str = ""  # empty = same as model
    fallback_models: list[str] = field(default_factory=list)  # tried in order when the main model is busy or down
    rpm: float = 18.0  # OpenRouter free models allow 20 requests/minute
    concurrency: int = 4
    web_search: bool = False  # OpenRouter web plugin, billed per result
    web_results: int = 3
    max_pages: int = 4
    max_chars_per_page: int = 5000
    contacts_per_company: int = 2
    app_title: str = "leadagent"

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        s = cls(
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            base_url=os.environ.get("LEADAGENT_BASE_URL", cls.base_url),
            model=os.environ.get("LEADAGENT_MODEL", cls.model),
            writer_model=os.environ.get("LEADAGENT_WRITER_MODEL", ""),
            fallback_models=[m.strip() for m in os.environ.get("LEADAGENT_FALLBACK_MODELS", "").split(",") if m.strip()],
            rpm=float(os.environ.get("LEADAGENT_RPM", cls.rpm)),
            concurrency=int(os.environ.get("LEADAGENT_CONCURRENCY", cls.concurrency)),
            web_search=_env_bool("LEADAGENT_WEB_SEARCH", cls.web_search),
            max_pages=int(os.environ.get("LEADAGENT_MAX_PAGES", cls.max_pages)),
        )
        for k, v in overrides.items():
            if v is not None:
                setattr(s, k, v)
        return s
