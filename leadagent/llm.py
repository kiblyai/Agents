"""Kept for backward compatibility: the model client now lives in agentkit.llm."""

from agentkit.llm import (  # noqa: F401
    LLM,
    RETRYABLE,
    DailyLimitError,
    LLMOutputError,
    RateLimiter,
    Usage,
    extract_json,
)
