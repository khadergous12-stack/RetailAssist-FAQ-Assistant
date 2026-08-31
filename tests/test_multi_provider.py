from __future__ import annotations

from types import SimpleNamespace

import pytest

from config.settings import Settings
from providers.factory import create_generator
from providers.openai.generator import OpenAIGenerator
from rag.contracts import Generator
from rag.service import RAGResponse, RAGService


def test_provider_factory_creates_openrouter_generator(monkeypatch):
    """Factory should return the second provider implementation."""

    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_model="test-model",
        openrouter_max_tokens=128,
        openrouter_timeout=30.0,
    )

    generator = create_generator(
        provider_name="openrouter",
        settings=settings,
    )

    assert isinstance(generator, OpenAIGenerator)


def test_provider_factory_creates_snowflake_generator():
    """Factory should preserve Snowflake Cortex generation."""

    class FakeSession:
        pass

    generator = create_generator(
        provider_name="snowflake",
        session=FakeSession(),
    )

    assert generator is not None
    assert callable(getattr(generator, "generate", None))


def test_provider_factory_rejects_unsupported_provider():
    """Unsupported provider names should produce a clear error."""

    with pytest.raises(
        ValueError,
        match="Unsupported AI provider",
    ):
        create_generator(
            provider_name="unsupported-provider",
        )


def test_openrouter_missing_api_key():
    """Missing OpenRouter credentials should be reported clearly."""

    settings = Settings(
        openrouter_api_key=None,
        openrouter_model="test-model",
        openrouter_max_tokens=128,
        openrouter_timeout=30.0,
    )

    with pytest.raises(
        ValueError,
        match="OpenRouter API key is not configured",
    ):
        OpenAIGenerator(settings=settings)


def test_openrouter_missing_model():
    """Missing OpenRouter model configuration should be reported clearly."""

    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_model=None,
        openrouter_max_tokens=128,
        openrouter_timeout=30.0,
    )

    with pytest.raises(
        ValueError,
        match="OpenRouter model is not configured",
    ):
        OpenAIGenerator(settings=settings)


def test_openrouter_implements_generator_contract():
    """Second provider must expose the same Generator interface."""

    assert callable(
        getattr(
            OpenAIGenerator,
            "generate",
            None,
        )
    )


def test_openrouter_generation_success(monkeypatch):
    """Successful provider calls should return generated text."""

    class FakeCompletions:
        def create(self, **kwargs):
            assert kwargs["model"] == "test-model"
            assert kwargs["max_tokens"] == 128
            assert kwargs["messages"][0]["content"] == "test prompt"

            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="Grounded test answer.")
                    )
                ]
            )

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_model="test-model",
        openrouter_max_tokens=128,
        openrouter_timeout=30.0,
    )

    generator = OpenAIGenerator(settings=settings)

    generator.client = FakeClient()

    result = generator.generate("test prompt")

    assert result == "Grounded test answer."


def test_openrouter_api_failure_is_wrapped():
    """Provider failures should become a user-safe RuntimeError."""

    class FakeCompletions:
        def create(self, **kwargs):
            raise RuntimeError("simulated provider failure")

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_model="test-model",
        openrouter_max_tokens=128,
        openrouter_timeout=30.0,
    )

    generator = OpenAIGenerator(settings=settings)

    generator.client = FakeClient()

    with pytest.raises(
        RuntimeError,
        match="OpenRouter generation failed",
    ):
        generator.generate("test prompt")


def test_rag_service_uses_selected_generator():
    """RAGService should remain generator-neutral."""

    class FakeRetriever:
        def retrieve(self, query: str, top_k: int = 5):
            from rag.contracts import RetrievedChunk

            return [
                RetrievedChunk(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    document_name="test.md",
                    category="test",
                    chunk_index=0,
                    chunk_text=(
                        "## Test policy\n\nThe answer is available in the policy."
                    ),
                    score=1.0,
                    page_number=1,
                    source_type="USER_UPLOAD",
                    section_heading="Test policy",
                )
            ]

    class FakeGenerator:
        def __init__(self):
            self.received_prompt = None

        def generate(self, prompt: str) -> str:
            self.received_prompt = prompt
            return "The answer is available in the policy."

    generator = FakeGenerator()

    service = RAGService(
        retriever=FakeRetriever(),
        generator=generator,
    )

    response = service.answer(
        "What is the text policy?",
        top_k=1,
    )

    assert isinstance(response, RAGResponse)
    assert response.answer == "The answer is available in the policy."
    assert len(response.evidence) == 1
    assert generator.received_prompt is not None
    assert "The answer is available in the policy." in generator.received_prompt
