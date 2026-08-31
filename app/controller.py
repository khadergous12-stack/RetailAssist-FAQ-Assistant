from __future__ import annotations

from rag.service import RAGResponse, RAGService
from providers.factory import create_generator


class RetailAssistController:
    """
    Application controller for RetailAssist.

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
            raise ValueError("AI provider cannot be empty.")

        generator = create_generator(
            provider_name=provider,
            session=self.session,
        )

        self.rag_service.generator = generator
        self.selected_provider = provider

    def get_provider(self) -> str:
        """Return the currently selected generation provider."""
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
            raise ValueError("Question cannot be empty.")

        return self.rag_service.answer(
            question=question.strip(),
            top_k=top_k,
        )
