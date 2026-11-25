from __future__ import annotations

import logging
import time

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
        start = time.perf_counter()

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
            "RetailAssist controller created | provider=%s | duration=%.3fs",
            self.selected_provider,
            time.perf_counter() - start,
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
        start = time.perf_counter()
        provider = str(provider_name or "").strip().lower()

        if not provider:
            logger.warning(
                "Policy RAG provider switch rejected | reason=empty_provider | duration=%.3fs",
                time.perf_counter() - start,
            )
            raise ValueError("AI provider cannot be empty.")

        logger.info(
            "Policy RAG provider switch started | from=%s | to=%s",
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
                "Policy RAG provider switch completed | provider=%s | duration=%.3fs",
                provider,
                time.perf_counter() - start,
            )
        except Exception as exc:
            logger.exception(
                "Policy RAG provider switch failed | provider=%s | exception=%s | duration=%.3fs",
                provider,
                type(exc).__name__,
                time.perf_counter() - start,
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
        start = time.perf_counter()

        if not question or not question.strip():
            logger.warning(
                "Policy RAG request rejected | reason=empty_question | duration=%.3fs",
                time.perf_counter() - start,
            )
            raise ValueError("Question cannot be empty.")

        cleaned_question = question.strip()

        logger.info(
            "Policy RAG request started | provider=%s | top_k=%s | question_length=%s",
            self.selected_provider,
            top_k,
            len(cleaned_question),
        )

        try:
            response = self.rag_service.answer(
                question=cleaned_question,
                top_k=top_k,
            )

            total_duration = time.perf_counter() - start

            logger.info(
                "Policy RAG request completed | provider=%s | answer_length=%s | evidence_count=%s | duration=%.3fs",
                self.selected_provider,
                len(response.answer or ""),
                len(response.evidence or []),
                total_duration,
            )

            if total_duration >= 10:
                logger.warning(
                    "Slow Policy RAG controller request | provider=%s | duration=%.3fs",
                    self.selected_provider,
                    total_duration,
                )

            return response

        except Exception as exc:
            logger.exception(
                "Policy RAG request failed | provider=%s | exception=%s | duration=%.3fs",
                self.selected_provider,
                type(exc).__name__,
                time.perf_counter() - start,
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
        start = time.perf_counter()

        if not question or not question.strip():
            logger.warning(
                "Support Analytics request rejected | reason=empty_question | duration=%.3fs",
                time.perf_counter() - start,
            )
            raise ValueError("Question cannot be empty.")

        cleaned_question = question.strip()

        logger.info(
            "Support Analytics controller request started | question_length=%s",
            len(cleaned_question),
        )

        try:
            response = self.analyst_service.ask(
                cleaned_question,
            )

            total_duration = time.perf_counter() - start

            logger.info(
                "Support Analytics controller request completed | answer_length=%s | sql_present=%s | result_rows=%s | duration=%.3fs",
                len(response.answer or ""),
                bool(response.sql),
                len(response.result or []),
                total_duration,
            )

            if total_duration >= 10:
                logger.warning(
                    "Slow Support Analytics controller request | duration=%.3fs",
                    total_duration,
                )

            return response

        except Exception as exc:
            logger.exception(
                "Support Analytics controller request failed | exception=%s | duration=%.3fs",
                type(exc).__name__,
                time.perf_counter() - start,
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
        start = time.perf_counter()

        if not question or not question.strip():
            logger.warning(
                "Cortex Agent controller request rejected | reason=empty_question | duration=%.3fs",
                time.perf_counter() - start,
            )
            raise ValueError("Question cannot be empty.")

        cleaned_question = question.strip()

        logger.info(
            "Cortex Agent controller request started | question_length=%s",
            len(cleaned_question),
        )

        try:
            response = self.agent_service.ask(
                cleaned_question,
            )

            total_duration = time.perf_counter() - start
            used_tools = response.used_tools or []

            logger.info(
                "Cortex Agent controller request completed | answer_length=%s | tool_count=%s | tools=%s | duration=%.3fs",
                len(response.answer or ""),
                len(used_tools),
                ",".join(used_tools) if used_tools else "none",
                total_duration,
            )

            if total_duration >= 10:
                logger.warning(
                    "Slow Cortex Agent controller request | duration=%.3fs",
                    total_duration,
                )

            return response

        except Exception as exc:
            logger.exception(
                "Cortex Agent controller request failed | exception=%s | duration=%.3fs",
                type(exc).__name__,
                time.perf_counter() - start,
            )
            raise
