"""Model settings and small command-line helpers shared by the agents."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass
class Settings:
    """Model settings: an agent's own PREFIX_* variables, falling back to the LEADAGENT_* ones."""

    api_key: str = field(default="", repr=False)
    base_url: str = DEFAULT_BASE_URL
    model: str = "openrouter/free"
    fallback_models: list[str] = field(default_factory=list)
    rpm: float = 18.0
    app_title: str = "agent"

    @classmethod
    def from_env(cls, prefix: str, app_title: str = "") -> "Settings":
        """PREFIX_MODEL, PREFIX_FALLBACK_MODELS, PREFIX_BASE_URL, PREFIX_RPM and PREFIX_API_KEY win over the
        LEADAGENT_* settings and OPENROUTER_API_KEY."""
        env = os.environ.get

        def pick(name: str) -> str:
            return env(f"{prefix}_{name}") or env(f"LEADAGENT_{name}") or ""

        return cls(
            api_key=env(f"{prefix}_API_KEY") or env("OPENROUTER_API_KEY", ""),
            base_url=pick("BASE_URL") or DEFAULT_BASE_URL,
            model=pick("MODEL") or cls.model,
            fallback_models=[m.strip() for m in pick("FALLBACK_MODELS").split(",") if m.strip()],
            rpm=float(pick("RPM") or cls.rpm),
            app_title=app_title or prefix.lower(),
        )


def is_free_model(model: str) -> bool:
    return model.endswith(":free") or model.split(":")[0] == "openrouter/free"


def free_model_warning(prefix: str, real_data: str) -> str:
    """The warning printed when a free model is selected for an agent that may see a client's data."""
    return (f"WARNING: free models may log prompts. Use them only with the synthetic example, never {real_data}. "
            f"For client work set {prefix}_MODEL to a paid model and turn on zero data retention in your OpenRouter "
            "privacy settings.\n")


def check_key(s: Settings) -> None:
    if not s.api_key:
        sys.exit("OPENROUTER_API_KEY is not set. Put it in your environment or a local .env file.")


def require(*paths: str | Path | None) -> None:
    """Stop with a plain message when an input file or folder is missing."""
    missing = [str(p) for p in paths if p and not Path(p).exists()]
    if missing:
        sys.exit("Not found: " + ", ".join(missing) + f"\n(current folder: {Path.cwd()})")


def plural(n: int, word: str, many: str = "") -> str:
    return f"{n} {word if n == 1 else many or word + 's'}"
