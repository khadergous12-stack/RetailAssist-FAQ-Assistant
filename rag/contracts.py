from dataclasses import dataclass
from typing import Protocol


@dataclass
class RetrievedChunk:
    """A provider-neutral piece of retrieved policy evidence."""

    chunk_id: str
    document_id: str
    document_name: str
    category: str
    chunk_index: int
    chunk_text: str
    score: float | None = None


class Retriever(Protocol):
    """Interface used by the RAG service for evidence retrieval."""

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[RetrievedChunk]: ...


class Generator(Protocol):
    """Interface used by the RAG service for answer generation."""

    def generate(
        self,
        prompt: str,
    ) -> str: ...
