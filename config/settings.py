from __future__ import annotations

import os

from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Central application configuration."""

    # Application
    retail_assist_mode: str = "SNOWFLAKE"
    default_ai_provider: str = "snowflake"
    log_level: str = "INFO"

    # Groq
    groq_api_key: str | None = None
    groq_model: str | None = None
    groq_max_tokens: int = 512
    groq_timeout: float = 60.0


def load_settings() -> Settings:
    """Load application configuration from environment variables."""

    max_tokens_raw = os.environ.get(
        "GROQ_MAX_TOKENS",
        "512",
    )

    try:
        max_tokens = int(max_tokens_raw)
    except ValueError as exc:
        raise ValueError("GROQ_MAX_TOKENS must be an integer.") from exc

    if max_tokens <= 0:
        raise ValueError("GROQ_MAX_TOKENS must be greater than zero.")

    timeout_raw = os.environ.get(
        "GROQ_TIMEOUT",
        "60",
    )

    try:
        timeout = float(timeout_raw)
    except ValueError as exc:
        raise ValueError("GROQ_TIMEOUT must be a number.") from exc

    if timeout <= 0:
        raise ValueError("GROQ_TIMEOUT must be greater than zero.")

    return Settings(
        retail_assist_mode=os.environ.get(
            "RETAIL_ASSIST_MODE",
            "SNOWFLAKE",
        )
        .strip()
        .upper(),
        default_ai_provider=os.environ.get(
            "DEFAULT_AI_PROVIDER",
            "snowflake",
        )
        .strip()
        .lower(),
        log_level=os.environ.get(
            "RETAIL_ASSIST_LOG_LEVEL",
            "INFO",
        )
        .strip()
        .upper(),
        groq_api_key=os.environ.get("GROQ_API_KEY"),
        groq_model=os.environ.get("GROQ_MODEL"),
        groq_max_tokens=max_tokens,
        groq_timeout=timeout,
    )
