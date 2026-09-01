from __future__ import annotations

import logging

from rag.service import RAGResponse, RAGService
from providers.factory import create_generator
from config.settings import load_settings


logger = logging.getLogger(__name__)


class RetailAssistController:
    """
    Application controller for Support AI.

    The controller coordinates:
      - provider-neutral RAG orchestration
      - runtime generator selection
      - document ingestion/management

    The UI only provides the provider name. Concrete generator creation
    remains inside the provider factory.
    """

    def __init__(
        self,
        rag_service: RAGService,
        document_store=None,
        session=None,
    ):
        self.rag_service = rag_service
        self.document_store = document_store
        self.session = session

        self.selected_provider = "snowflake"

        logger.info(
            "Support AI controller created. Initial provider=%s",
            self.selected_provider,
        )

    def set_provider(
        self,
        provider_name: str,
    ) -> None:
        """
        Select the generation provider for subsequent requests.

        Retrieval remains unchanged because the existing Retriever is reused
        by the RAG service.
        """

        provider = str(provider_name or "").strip().lower()

        if not provider:
            logger.error("Provider selection failed: provider name is empty.")
            raise ValueError("AI provider cannot be empty.")

        logger.info(
            "Switching AI generation provider from %s to %s.",
            self.selected_provider,
            provider,
        )

        try:
            settings = load_settings()

            generator = create_generator(
                provider_name=provider,
                session=self.session,
                settings=settings,
            )

            self.rag_service.generator = generator
            self.selected_provider = provider

            logger.info(
                "AI generation provider switched successfully. Provider=%s",
                provider,
            )

        except Exception:
            logger.exception(
                "Failed to initialize AI generation provider. Provider=%s",
                provider,
            )
            raise

    def get_provider(self) -> str:
        """Return the currently selected generation provider."""

        logger.debug(
            "Current AI generation provider requested. Provider=%s",
            self.selected_provider,
        )

        return self.selected_provider

    def ask(
        self,
        question: str,
        top_k: int = 5,
    ) -> RAGResponse:
        """
        Process a customer question through the provider-neutral RAG service.
        """

        if not question or not question.strip():
            logger.warning("Question rejected because it was empty.")
            raise ValueError("Question cannot be empty.")

        cleaned_question = question.strip()

        logger.info(
            "RAG request started. Provider=%s, top_k=%s, question_length=%s",
            self.selected_provider,
            top_k,
            len(cleaned_question),
        )

        try:
            response = self.rag_service.answer(
                question=cleaned_question,
                top_k=top_k,
            )

            logger.info(
                "RAG request completed successfully. Provider=%s, "
                "answer_length=%s, evidence_count=%s",
                self.selected_provider,
                len(response.answer or ""),
                len(response.evidence or []),
            )

            return response

        except Exception:
            logger.exception(
                "RAG request failed. Provider=%s, question_length=%s",
                self.selected_provider,
                len(cleaned_question),
            )
            raise
