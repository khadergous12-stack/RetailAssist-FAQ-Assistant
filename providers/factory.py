from __future__ import annotations

from config.settings import Settings, load_settings
from rag.contracts import Generator
from providers.snowflake.generator import SnowflakeGenerator
from providers.openai.generator import OpenAIGenerator


def create_generator(
    provider_name: str,
    session=None,
    settings: Settings | None = None,
) -> Generator:
    """
    Create the selected generation provider.

    Retrieval remains exclusively in Snowflake Cortex Search.
    """

    provider = str(provider_name or "").strip().lower()
    settings = settings or load_settings()

    if provider in {
        "snowflake",
        "snowflake cortex",
        "cortex",
    }:
        return SnowflakeGenerator(
            session=session,
        )

    if provider in {
        "groq",
    }:
        return OpenAIGenerator(
            settings=settings,
        )

    raise ValueError(f"Unsupported AI provider: {provider_name}")
