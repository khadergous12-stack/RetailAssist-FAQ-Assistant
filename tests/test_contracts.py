from rag.contracts import Generator, RetrievedChunk, Retriever


class FakeRetriever:
    def retrieve(self, query: str, top_k: int = 5):
        return []


class FakeGenerator:
    def generate(self, prompt: str) -> str:
        return "test answer"


def test_retrieved_chunk_structure():
    chunk = RetrievedChunk(
        chunk_id="TEST_001",
        document_id="TEST_DOC",
        document_name="Test FAQ",
        category="test",
        chunk_index=0,
        chunk_text="Example policy text.",
    )

    assert chunk.chunk_id == "TEST_001"
    assert chunk.document_id == "TEST_DOC"
    assert chunk.document_name == "Test FAQ"
    assert chunk.category == "test"
    assert chunk.chunk_index == 0
    assert chunk.chunk_text == "Example policy text."


def test_fake_retriever_matches_contract():
    retriever: Retriever = FakeRetriever()

    results = retriever.retrieve(
        query="test question",
        top_k=5,
    )

    assert isinstance(results, list)


def test_fake_generator_matches_contract():
    generator: Generator = FakeGenerator()

    answer = generator.generate("test prompt")

    assert answer == "test answer"
