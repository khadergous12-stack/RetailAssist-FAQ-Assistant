from __future__ import annotations

import logging

from rag.service import RAGResponse, RAGService
from providers.factory import create_generator
from config.settings import load_settings

from services.cortex_analyst import (
    CortexAnalystResponse,
    CortexAnalystService,
)
from services.cortex_agent import (
    CortexAgentResponse,
    CortexAgentService,
)


logger = logging.getLogger(__name__)


class RetailAssistController:
    """
    Application controller for RetailAssist.

    Responsibilities:
        - Provider selection for Policy RAG
        - Existing RAG flow
        - Cortex Analyst support analytics
        - Cortex Agent orchestration
        - Document management
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

        # Existing Policy RAG provider.
        # Keep the controller's provider state synchronized with the
        # generator that was actually created by main.py.
        settings = load_settings()
        configured_provider = (
            str(settings.default_ai_provider or "snowflake").strip().lower()
        )

        # The Groq generator is exposed through the existing "openai"
        # provider name in the provider factory/UI mapping.
        if configured_provider == "groq":
            configured_provider = "openai"

        self.selected_provider = configured_provider

        # Snowflake-native analytics services.
        self.analyst_service = CortexAnalystService()
        self.agent_service = CortexAgentService()

        logger.info(
            "RetailAssist controller created. Initial provider=%s",
            self.selected_provider,
        )

    # ============================================================
    # POLICY RAG / PROVIDER SELECTION
    # ============================================================

    def set_provider(
        self,
        provider_name: str,
    ) -> None:
        """
        Select the generation provider for Policy RAG.

        This affects ONLY the Policy RAG generation path.
        Cortex Analyst and Cortex Agent do not use this provider.
        """

        provider = str(provider_name or "").strip().lower()

        if not provider:
            raise ValueError("AI provider cannot be empty.")

        logger.info(
            "Switching Policy RAG provider from %s to %s.",
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
                "Policy RAG provider switched successfully. Provider=%s",
                provider,
            )

        except Exception:
            logger.exception(
                "Failed to initialize Policy RAG provider. Provider=%s",
                provider,
            )
            raise

    def get_provider(self) -> str:
        """Return the currently selected Policy RAG provider."""

        return self.selected_provider

    # ============================================================
    # POLICY RAG
    # ============================================================

    def ask(
        self,
        question: str,
        top_k: int = 5,
    ) -> RAGResponse:
        """
        Process a question through the existing Policy RAG flow.

        This method is intentionally preserved for backward
        compatibility with the existing application.
        """

        if not question or not question.strip():
            raise ValueError("Question cannot be empty.")

        cleaned_question = question.strip()

        logger.info(
            "Policy RAG request started. Provider=%s, top_k=%s",
            self.selected_provider,
            top_k,
        )

        try:
            response = self.rag_service.answer(
                question=cleaned_question,
                top_k=top_k,
            )

            logger.info(
                "Policy RAG request completed. Provider=%s, "
                "answer_length=%s, evidence_count=%s",
                self.selected_provider,
                len(response.answer or ""),
                len(response.evidence or []),
            )

            return response

        except Exception:
            logger.exception(
                "Policy RAG request failed. Provider=%s",
                self.selected_provider,
            )
            raise

    def ask_policy(
        self,
        question: str,
        top_k: int = 5,
    ) -> RAGResponse:
        """
        Explicit Policy RAG entry point.

        This is an alias around the existing ask() method so the
        UI can clearly distinguish Policy RAG from other modes.
        """

        return self.ask(
            question=question,
            top_k=top_k,
        )

    # ============================================================
    # SUPPORT ANALYTICS
    # ============================================================

    def ask_analytics(
        self,
        question: str,
    ) -> CortexAnalystResponse:
        """
        Process a natural-language support analytics question
        through Snowflake Cortex Analyst.

        The question is answered using the SUPPORT_ANALYTICS
        Semantic View.
        """

        if not question or not question.strip():
            raise ValueError("Question cannot be empty.")

        cleaned_question = question.strip()

        logger.info(
            "Support Analytics request started. question_length=%s",
            len(cleaned_question),
        )

        try:
            response = self.analyst_service.ask(
                cleaned_question,
            )

            logger.info(
                "Support Analytics request completed. answer_length=%s, sql_present=%s",
                len(response.answer or ""),
                bool(response.sql),
            )

            return response

        except Exception:
            logger.exception(
                "Support Analytics request failed.",
            )
            raise

    # ============================================================
    # CORTEX AGENT
    # ============================================================

    def ask_agent(
        self,
        question: str,
    ) -> CortexAgentResponse:
        """
        Process a question through the Snowflake Cortex Agent.

        The Agent automatically decides whether to use:
            - PolicySearch
            - SupportAnalytics
            - both tools
        """

        if not question or not question.strip():
            raise ValueError("Question cannot be empty.")

        cleaned_question = question.strip()

        logger.info(
            "Cortex Agent request started. question_length=%s",
            len(cleaned_question),
        )

        try:
            response = self.agent_service.ask(
                cleaned_question,
            )

            logger.info(
                "Cortex Agent request completed. answer_length=%s",
                len(response.answer or ""),
            )

            return response

        except Exception:
            logger.exception(
                "Cortex Agent request failed.",
            )
            raise
