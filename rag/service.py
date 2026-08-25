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

    Retrieves candidate chunks, filters them using question intent
    and category relevance, removes duplicates, and only passes
    relevant evidence to the generator and UI.
    """

    MAX_EVIDENCE = 3

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
        "may",
        "might",
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
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "how",
        "and",
        "or",
        "but",
        "if",
        "for",
        "to",
        "of",
        "from",
        "on",
        "in",
        "at",
        "by",
        "with",
        "about",
        "as",
        "into",
        "through",
        "after",
        "before",
        "during",
        "than",
        "then",
        "have",
        "has",
        "had",
        "not",
        "please",
        "tell",
        "provide",
        "question",
        "customer",
        "customers",
        "retailassist",
    }

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

    CATEGORY_KEYWORDS = {
        "refunds": [
            "refund",
            "refunded",
            "refunds",
            "reimbursement",
            "money back",
            "refund sent",
            "refund pending",
            "refund arrived",
            "refund not arrived",
            "refund not received",
            "refund has not arrived",
            "refund has not appeared",
            "refund not appeared",
        ],
        "returns": [
            "return",
            "returns",
            "returned",
            "send it back",
            "exchange",
            "replace",
            "replacement",
            "return window",
            "return policy",
            "return an item",
        ],
        "shipping": [
            "shipping",
            "delivery",
            "delivered",
            "shipment",
            "dispatch",
            "courier",
            "tracking",
            "delivery arrived",
            "delivery not arrived",
            "order arrived",
        ],
        "warranty": [
            "warranty",
            "damaged",
            "damage",
            "broken",
            "cracked",
            "defective",
            "screen",
            "repair",
            "covered",
            "accidental damage",
        ],
        "payments": [
            "card",
            "payment",
            "payments",
            "declined",
            "charge",
            "charged",
            "checkout",
            "transaction",
            "payment failed",
            "pay",
            "payment method",
            "payment methods",
        ],
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
        # Step 1: Retrieve candidate chunks
        # ---------------------------------------------------------

        retrieved = self.retriever.retrieve(
            query=question,
            top_k=top_k,
        )

        # ---------------------------------------------------------
        # Step 2: Filter evidence
        # ---------------------------------------------------------

        evidence = self._filter_evidence(
            question=question,
            chunks=retrieved,
        )

        # ---------------------------------------------------------
        # Step 3: Refuse unsupported questions
        # ---------------------------------------------------------

        if not evidence:
            return RAGResponse(
                answer=REFUSAL_MESSAGE,
                evidence=[],
            )

        # ---------------------------------------------------------
        # Step 4: Format accepted evidence
        # ---------------------------------------------------------

        evidence_text = self._format_evidence(evidence)

        # ---------------------------------------------------------
        # Step 5: Build grounded prompt
        # ---------------------------------------------------------

        grounded_prompt = build_grounded_prompt(
            question,
            evidence_text,
        )

        prompt = SYSTEM_PROMPT + "\n\n" + grounded_prompt

        # ---------------------------------------------------------
        # Step 6: Generate answer
        # ---------------------------------------------------------

        answer = self.generator.generate(prompt)

        if not answer or not answer.strip():
            return RAGResponse(
                answer=REFUSAL_MESSAGE,
                evidence=[],
            )

        answer = answer.strip()

        # ---------------------------------------------------------
        # Step 7: Generator refusal
        # ---------------------------------------------------------

        if answer == REFUSAL_MESSAGE:
            return RAGResponse(
                answer=REFUSAL_MESSAGE,
                evidence=[],
            )

        # ---------------------------------------------------------
        # Step 8: Grounding validation
        # ---------------------------------------------------------

        if not self._answer_is_grounded(
            answer,
            evidence_text,
        ):
            answer = self._fallback_evidence_answer(evidence)

            if not answer:
                return RAGResponse(
                    answer=REFUSAL_MESSAGE,
                    evidence=[],
                )

        return RAGResponse(
            answer=answer,
            evidence=evidence,
        )

    # =============================================================
    # CATEGORY DETECTION
    # =============================================================

    @classmethod
    def _detect_category(
        cls,
        question: str,
    ) -> str | None:
        """
        Detect the strongest policy category from the question.
        """

        text = question.lower().strip()

        scores = {category: 0 for category in cls.CATEGORY_KEYWORDS}

        for category, keywords in cls.CATEGORY_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    if " " in keyword:
                        scores[category] += 3
                    else:
                        scores[category] += 1

        ranked = sorted(
            scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )

        if not ranked:
            return None

        if ranked[0][1] == 0:
            return None

        if len(ranked) == 1:
            return ranked[0][0]

        if ranked[0][1] > ranked[1][1]:
            return ranked[0][0]

        return None

    # =============================================================
    # EVIDENCE FILTERING
    # =============================================================

    @classmethod
    def _filter_evidence(
        cls,
        question: str,
        chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """
        Keep only evidence that is actually relevant to the question.

        Rules:
        - Empty chunks are ignored.
        - If the question has a clear category, other categories
          are rejected.
        - Meaningful question terms must overlap with the chunk.
        - Phrase matches receive additional weight.
        - Duplicate chunks/content are removed.
        - Maximum three sources are returned.
        """

        if not question or not chunks:
            return []

        valid_chunks = [
            chunk for chunk in chunks if chunk.chunk_text and chunk.chunk_text.strip()
        ]

        if not valid_chunks:
            return []

        question_category = cls._detect_category(question)

        question_terms = cls._meaningful_words(question)

        if not question_terms:
            return []

        scored_chunks = []

        for position, chunk in enumerate(valid_chunks):
            chunk_category = (chunk.category or "").strip().lower()

            # -----------------------------------------------------
            # HARD CATEGORY FILTER
            # -----------------------------------------------------

            if question_category and chunk_category:
                if chunk_category != question_category:
                    continue

            # -----------------------------------------------------
            # Build searchable chunk text
            # -----------------------------------------------------

            chunk_text = (
                str(chunk.document_name or "")
                + " "
                + chunk_category
                + " "
                + str(chunk.chunk_text or "")
            )

            chunk_terms = cls._meaningful_words(chunk_text)

            overlap = question_terms.intersection(chunk_terms)

            # -----------------------------------------------------
            # Phrase matching
            # -----------------------------------------------------

            phrase_matches = cls._find_phrase_matches(
                question,
                str(chunk.chunk_text or ""),
            )

            # -----------------------------------------------------
            # A source MUST contain meaningful overlap.
            # -----------------------------------------------------

            if not overlap and not phrase_matches:
                continue

            # -----------------------------------------------------
            # Calculate relevance
            # -----------------------------------------------------

            relevance = 0.0

            relevance += len(overlap) * 2.0

            relevance += len(phrase_matches) * 3.0

            # Slight preference for Cortex's original ranking.
            relevance += max(
                0.0,
                0.30 - (position * 0.05),
            )

            # Category match is useful but never enough by itself.
            if question_category and chunk_category == question_category:
                relevance += 1.0

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
        # Sort by relevance
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

        # ---------------------------------------------------------
        # Remove duplicates
        # ---------------------------------------------------------

        selected = []

        seen_chunk_ids = set()
        seen_content = set()

        for (
            relevance,
            overlap_count,
            phrase_count,
            cortex_score,
            position,
            chunk,
        ) in scored_chunks:
            if len(selected) >= cls.MAX_EVIDENCE:
                break

            if overlap_count == 0 and phrase_count == 0:
                continue

            chunk_id = str(chunk.chunk_id or "")

            normalized_content = cls._normalize(str(chunk.chunk_text or ""))

            if chunk_id and chunk_id in seen_chunk_ids:
                continue

            if normalized_content in seen_content:
                continue

            selected.append(chunk)

            if chunk_id:
                seen_chunk_ids.add(chunk_id)

            seen_content.add(normalized_content)

        return selected

    # =============================================================
    # TEXT NORMALIZATION
    # =============================================================

    @classmethod
    def _meaningful_words(
        cls,
        text: str,
    ) -> set[str]:
        """
        Extract meaningful searchable words.
        """

        if not text:
            return set()

        tokens = re.findall(
            r"[a-zA-Z0-9]+",
            text.lower(),
        )

        words = set()

        for token in tokens:
            if len(token) < 3:
                continue

            normalized = cls._normalize_word(token)

            if len(normalized) < 3:
                continue

            if normalized in cls.GENERIC_TERMS:
                continue

            if normalized in cls.CATEGORY_TERMS:
                continue

            if normalized in cls.POLICY_TERMS:
                continue

            words.add(normalized)

        return words

    @staticmethod
    def _normalize_word(
        word: str,
    ) -> str:
        """
        Lightweight stemming for FAQ wording.
        """

        if not word:
            return ""

        word = word.lower()

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

    @staticmethod
    def _normalize(
        text: str,
    ) -> str:
        """
        Normalize arbitrary text for duplicate/phrase comparison.
        """

        if not text:
            return ""

        return re.sub(
            r"[^a-z0-9]+",
            " ",
            text.lower(),
        ).strip()

    # =============================================================
    # PHRASE MATCHING
    # =============================================================

    @classmethod
    def _find_phrase_matches(
        cls,
        question: str,
        chunk_text: str,
    ) -> set[str]:
        """
        Find meaningful two-word phrases from the question
        inside the retrieved chunk.
        """

        question_tokens = re.findall(
            r"[a-zA-Z0-9]+",
            question.lower(),
        )

        normalized_tokens = [
            cls._normalize_word(token) for token in question_tokens if len(token) >= 3
        ]

        chunk_normalized = cls._normalize(chunk_text)

        phrases = set()

        for index in range(len(normalized_tokens) - 1):
            first = normalized_tokens[index]
            second = normalized_tokens[index + 1]

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

            phrase = first + " " + second

            if phrase in chunk_normalized:
                phrases.add(phrase)

        return phrases

    # =============================================================
    # EVIDENCE FORMATTING
    # =============================================================

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
            formatted_chunk = (
                "Evidence " + str(index) + "\n\n"
                "----------------\n\n"
                "Document: " + str(chunk.document_name) + "\n\n"
                "Category: " + str(chunk.category) + "\n\n"
                "Chunk ID: " + str(chunk.chunk_id) + "\n\n"
                "Chunk Index: " + str(chunk.chunk_index) + "\n\n"
                "Text:\n\n" + str(chunk.chunk_text)
            )

            formatted_chunks.append(formatted_chunk.strip())

        return "\n\n".join(formatted_chunks)

    # =============================================================
    # ANSWER GROUNDING
    # =============================================================

    @classmethod
    def _answer_is_grounded(
        cls,
        answer: str,
        evidence: str,
    ) -> bool:
        """
        Ensure generated answers do not introduce unsupported
        numbers or multiple unsupported factual terms.
        """

        if not answer or not evidence:
            return False

        answer_lower = answer.lower()
        evidence_lower = evidence.lower()

        # ---------------------------------------------------------
        # Numbers must exist in the evidence.
        # ---------------------------------------------------------

        answer_numbers = set(
            re.findall(
                r"\b\d+(?:[.,]\d+)?%?\b",
                answer_lower,
            )
        )

        evidence_numbers = set(
            re.findall(
                r"\b\d+(?:[.,]\d+)?%?\b",
                evidence_lower,
            )
        )

        if not answer_numbers.issubset(evidence_numbers):
            return False

        # ---------------------------------------------------------
        # Meaningful terms must be supported.
        # ---------------------------------------------------------

        answer_terms = cls._meaningful_words(answer_lower)

        evidence_terms = cls._meaningful_words(evidence_lower)

        unsupported_terms = answer_terms - evidence_terms

        # Allow a small amount of natural paraphrasing.
        if len(unsupported_terms) > 2:
            return False

        return True

    # =============================================================
    # FALLBACK ANSWER
    # =============================================================

    @staticmethod
    def _fallback_evidence_answer(
        chunks: list[RetrievedChunk],
    ) -> str:
        """
        Return the answer text directly from the best accepted
        FAQ chunk if generation is not sufficiently grounded.
        """

        if not chunks:
            return ""

        chunk_text = (chunks[0].chunk_text or "").strip()

        if not chunk_text:
            return ""

        lines = chunk_text.splitlines()

        # Remove Markdown heading if present.
        if lines and re.match(
            r"^\s*#{1,6}\s+.+?\s*$",
            lines[0],
        ):
            answer_text = "\n".join(lines[1:]).strip()

            if answer_text:
                return answer_text

        return chunk_text
