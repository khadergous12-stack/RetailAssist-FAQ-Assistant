from rag.service import RAGResponse, RAGService


class RetailAssistController:
    """
    Application controller for RetailAssist.

    The controller coordinates user requests with the
    provider-neutral RAG service and document ingestion.
    """

    def __init__(
        self,
        rag_service: RAGService,
        document_store=None,
    ):
        self.rag_service = rag_service
        self.document_store = document_store

    def ask(
        self,
        question: str,
        top_k: int = 5,
    ) -> RAGResponse:
        """
        Process a customer question through the RAG service.
        """

        if not question or not question.strip():
            raise ValueError("Question cannot be empty.")

        return self.rag_service.answer(
            question=question.strip(),
            top_k=top_k,
        )
