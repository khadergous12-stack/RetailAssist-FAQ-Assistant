from dataclasses import dataclass
import re

from rag.contracts import Generator, RetrievedChunk, Retriever

from rag.prompts import (
    REFUSAL_MESSAGE,
    SYSTEM_PROMPT,
    build_grounded_prompt,
)


@dataclass
class RAGResponse:
    """Final response returned by the RAG service."""

    answer: str
    evidence: list[RetrievedChunk]


class RAGService:
    """
    Provider-neutral RAG orchestration layer.

    Retrieves multiple candidate chunks, validates whether they
    actually address the customer's question, and only passes
    genuinely relevant evidence to the generator and UI.
    """

    MAX_EVIDENCE = 3

    # Words that are common in questions but do not identify
    # the actual subject of the customer's request.
    GENERIC_TERMS = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "can",
        "could",
        "would",
        "should",
        "do",
        "does",
        "did",
        "will",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "how",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "they",
        "their",
        "it",
        "this",
        "that",
        "these",
        "those",
        "to",
        "of",
        "for",
        "from",
        "on",
        "in",
        "at",
        "and",
        "or",
        "but",
        "if",
        "with",
        "about",
        "have",
        "has",
        "had",
        "please",
        "tell",
        "provide",
        "question",
        "policy",
        "policies",
        "customer",
        "customers",
        "retailassist",
    }

    # Words that identify the FAQ category but do not prove
    # that a particular chunk answers the question.
    CATEGORY_TERMS = {
        "refund",
        "refunds",
        "refunded",
        "reimbursement",
        "return",
        "returns",
        "returned",
        "returning",
        "shipping",
        "shipment",
        "delivery",
        "delivered",
        "warranty",
        "payment",
        "payments",
        "checkout",
        "transaction",
    }

    # Generic policy/action words. These should not independently
    # make an otherwise unrelated chunk look relevant.
    POLICY_TERMS = {
        "cover",
        "covered",
        "covers",
        "coverage",
        "offer",
        "offers",
        "available",
        "availability",
        "support",
        "supported",
        "provide",
        "provides",
        "allowed",
        "allow",
        "eligible",
        "policy",
        "standard",
        "method",
        "methods",
        "process",
        "cost",
        "costs",
        "pay",
        "pays",
        "paid",
        "charge",
        "charges",
        "charged",
        "required",
        "require",
        "requirements",
        "need",
        "needs",
    }

    def __init__(
        self,
        retriever: Retriever,
        generator: Generator,
    ):
        self.retriever = retriever
        self.generator = generator

    def answer(
        self,
        question: str,
        top_k: int = 5,
    ) -> RAGResponse:
        """
        Retrieve relevant policy chunks and generate a grounded answer.
        """

        if not question or not question.strip():
            raise ValueError("Question cannot be empty.")

        question = question.strip()

        # ---------------------------------------------------------
        # Step 1: Retrieve candidate policy chunks
        # ---------------------------------------------------------
        retrieved = self.retriever.retrieve(
            query=question,
            top_k=top_k,
        )

        # ---------------------------------------------------------
        # Step 2: Validate actual relevance
        # ---------------------------------------------------------
        evidence = self._filter_evidence(
            question,
            retrieved,
        )

        # ---------------------------------------------------------
        # Step 3: Unsupported question
        #
        # IMPORTANT:
        # If no genuinely relevant evidence exists, return NO
        # sources. This prevents unrelated Cortex results from
        # appearing underneath an unsupported answer.
        # ---------------------------------------------------------
        if not evidence:
            return RAGResponse(
                answer=REFUSAL_MESSAGE,
                evidence=[],
            )

        # ---------------------------------------------------------
        # Step 4: Assemble accepted evidence
        # ---------------------------------------------------------
        evidence_text = self._format_evidence(evidence)

        # ---------------------------------------------------------
        # Step 5: Build grounded generation prompt
        # ---------------------------------------------------------
        prompt = f"{SYSTEM_PROMPT}\n\n{build_grounded_prompt(question, evidence_text)}"

        # ---------------------------------------------------------
        # Step 6: Generate final answer
        # ---------------------------------------------------------
        answer = self.generator.generate(prompt)

        # ---------------------------------------------------------
        # Step 7: Safety check
        #
        # If the generator itself decides the evidence does not
        # answer the question, do not display misleading sources.
        # ---------------------------------------------------------
        if not answer or not answer.strip():
            return RAGResponse(
                answer=REFUSAL_MESSAGE,
                evidence=[],
            )

        if answer.strip() == REFUSAL_MESSAGE:
            return RAGResponse(
                answer=REFUSAL_MESSAGE,
                evidence=[],
            )

        return RAGResponse(
            answer=answer.strip(),
            evidence=evidence,
        )

    @classmethod
    def _filter_evidence(
        cls,
        question: str,
        chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """
        Keep only chunks that contain meaningful subject information
        related to the customer's question.

        Category words such as "warranty", "shipping", "returns",
        and "payments" are NOT enough to accept a source.

        Example:

            Question:
                "Does the warranty cover theft?"

            A chunk saying:
                "The standard warranty covers defects..."

            is rejected because "theft" is not present.

        But:

            Question:
                "Does the warranty cover accidental damage?"

            A chunk discussing:
                "accidental damage..."

            is accepted.

        Multiple genuinely relevant chunks are retained.
        """

        if not question or not chunks:
            return []

        question_terms = cls._extract_terms(question)

        # Remove generic/category/policy words.
        meaningful_question_terms = (
            question_terms - cls.GENERIC_TERMS - cls.CATEGORY_TERMS - cls.POLICY_TERMS
        )

        # If the question contains no identifiable subject,
        # do not guess and do not display arbitrary sources.
        if not meaningful_question_terms:
            return []

        scored_chunks = []

        for position, chunk in enumerate(chunks):
            if not chunk.chunk_text or not chunk.chunk_text.strip():
                continue

            chunk_text = chunk.chunk_text.lower()

            chunk_terms = cls._extract_terms(chunk_text)

            meaningful_chunk_terms = (
                chunk_terms - cls.GENERIC_TERMS - cls.CATEGORY_TERMS - cls.POLICY_TERMS
            )

            if not meaningful_chunk_terms:
                continue

            # -----------------------------------------------------
            # Direct subject overlap
            # -----------------------------------------------------
            overlap = meaningful_question_terms & meaningful_chunk_terms

            # -----------------------------------------------------
            # Phrase matching
            #
            # Handles subjects such as:
            # "student discount"
            # "accidental damage"
            # "proof of purchase"
            # "return shipping"
            # -----------------------------------------------------
            phrase_matches = cls._find_phrase_matches(
                question,
                chunk_text,
            )

            # A chunk MUST contain either:
            #
            # 1. a meaningful subject term, OR
            # 2. a meaningful phrase from the question.
            #
            # This is the critical protection against showing
            # generic warranty/payment/shipping chunks.
            if not overlap and not phrase_matches:
                continue

            # -----------------------------------------------------
            # Calculate relevance
            # -----------------------------------------------------
            relevance = 0.0

            relevance += len(overlap) * 2.0
            relevance += len(phrase_matches) * 3.0

            # Give a small preference to higher-ranked Cortex
            # results when two chunks are otherwise similarly
            # relevant.
            relevance += max(
                0.0,
                0.30 - (position * 0.05),
            )

            # -----------------------------------------------------
            # Cortex score, when available
            #
            # We do NOT rely on an absolute Cortex score threshold
            # because score ranges can vary between search setups.
            # It is only used as a tie-breaker.
            # -----------------------------------------------------
            cortex_score = chunk.score if chunk.score is not None else 0.0

            scored_chunks.append(
                (
                    relevance,
                    len(overlap),
                    len(phrase_matches),
                    cortex_score,
                    position,
                    chunk,
                )
            )

        if not scored_chunks:
            return []

        # ---------------------------------------------------------
        # Sort:
        # 1. meaningful relevance
        # 2. number of subject matches
        # 3. phrase matches
        # 4. Cortex score
        # 5. original retrieval position
        # ---------------------------------------------------------
        scored_chunks.sort(
            key=lambda item: (
                -item[0],
                -item[1],
                -item[2],
                -item[3],
                item[4],
            )
        )

        selected: list[RetrievedChunk] = []

        for (
            relevance,
            overlap_count,
            phrase_count,
            _,
            _,
            chunk,
        ) in scored_chunks:
            # At least one actual subject match is mandatory.
            if overlap_count == 0 and phrase_count == 0:
                continue

            selected.append(chunk)

            if len(selected) >= cls.MAX_EVIDENCE:
                break

        return selected

    @classmethod
    def _extract_terms(cls, text: str) -> set[str]:
        """
        Normalize text into meaningful searchable terms.

        Basic stemming is used so that:

            return / returned
            cover / covered / covers
            deliver / delivered / delivery

        can be matched more reliably.
        """

        if not text:
            return set()

        words = set()

        tokens = re.findall(
            r"[a-zA-Z0-9]+",
            text.lower(),
        )

        for token in tokens:
            if len(token) < 3:
                continue

            normalized = cls._normalize_word(token)

            if len(normalized) < 3:
                continue

            words.add(normalized)

        return words

    @staticmethod
    def _normalize_word(word: str) -> str:
        """
        Lightweight normalization for common FAQ wording.
        """

        if word.endswith("ies") and len(word) > 4:
            return word[:-3] + "y"

        if word.endswith("ing") and len(word) > 5:
            base = word[:-3]

            if len(base) > 3 and base[-1] == base[-2]:
                base = base[:-1]

            return base

        if word.endswith("ed") and len(word) > 4:
            return word[:-2]

        if word.endswith("es") and len(word) > 4:
            return word[:-2]

        if word.endswith("s") and len(word) > 4:
            return word[:-1]

        return word

    @classmethod
    def _find_phrase_matches(
        cls,
        question: str,
        chunk_text: str,
    ) -> set[str]:
        """
        Detect meaningful multi-word phrases from the question
        inside the retrieved chunk.

        Only phrases containing meaningful subject words are used.
        """

        question_tokens = re.findall(
            r"[a-zA-Z0-9]+",
            question.lower(),
        )

        meaningful_tokens = [
            cls._normalize_word(token) for token in question_tokens if len(token) >= 3
        ]

        phrases = set()

        # Check adjacent two-word phrases.
        for index in range(len(meaningful_tokens) - 1):
            first = meaningful_tokens[index]
            second = meaningful_tokens[index + 1]

            if (
                first in cls.GENERIC_TERMS
                or first in cls.CATEGORY_TERMS
                or first in cls.POLICY_TERMS
            ):
                continue

            if (
                second in cls.GENERIC_TERMS
                or second in cls.CATEGORY_TERMS
                or second in cls.POLICY_TERMS
            ):
                continue

            phrase = f"{first} {second}"

            if phrase in chunk_text:
                phrases.add(phrase)

        return phrases

    @staticmethod
    def _format_evidence(
        chunks: list[RetrievedChunk],
    ) -> str:
        """
        Convert accepted chunks into clearly labelled evidence.
        """

        formatted_chunks = []

        for index, chunk in enumerate(
            chunks,
            start=1,
        ):
            formatted_chunks.append(
                f"""
Evidence {index}

----------------

Document: {chunk.document_name}

Category: {chunk.category}

Chunk ID: {chunk.chunk_id}

Chunk Index: {chunk.chunk_index}

Text:

{chunk.chunk_text}
""".strip()
            )

        return "\n\n".join(formatted_chunks)
