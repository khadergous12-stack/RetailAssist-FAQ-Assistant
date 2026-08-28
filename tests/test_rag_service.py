from rag.contracts import RetrievedChunk
from rag.service import RAGService


class FakeRetriever:
    """Fake retriever used for offline testing."""

    def __init__(self, chunks):
        self.chunks = chunks

    def retrieve(self, query: str, top_k: int = 5):
        return self.chunks[:top_k]


class FakeGenerator:
    """Fake generator used for offline testing."""

    def __init__(self):
        self.last_prompt = None

    def generate(self, prompt: str) -> str:
        self.last_prompt = prompt
        return "This answer was generated from the supplied policy evidence."


def test_rag_service_grounds_generation():
    chunks = [
        RetrievedChunk(
            chunk_id="RETURNS_001",
            document_id="RETURNS",
            document_name="Returns FAQ",
            category="returns",
            chunk_index=0,
            chunk_text=(
                "Damaged products can be returned according to the returns policy."
            ),
        )
    ]

    retriever = FakeRetriever(chunks)
    generator = FakeGenerator()

    service = RAGService(
        retriever=retriever,
        generator=generator,
    )

    response = service.answer("My product arrived damaged. Can I return it?")

    assert response.answer
    assert len(response.evidence) == 1

    assert "Returns FAQ" in generator.last_prompt
    assert "Damaged products" in generator.last_prompt


def test_rag_service_refuses_when_no_evidence():
    retriever = FakeRetriever([])
    generator = FakeGenerator()

    service = RAGService(
        retriever=retriever,
        generator=generator,
    )

    response = service.answer("Do you offer same-day drone delivery?")

    assert response.answer == ("I couldn't find a policy that answers that question.")

    assert response.evidence == []
    assert generator.last_prompt is None
