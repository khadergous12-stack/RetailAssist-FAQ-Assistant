from __future__ import annotations

import json
import re

from rag.contracts import RetrievedChunk, Retriever
from providers.snowflake.connection import create_snowflake_session


class SnowflakeRetriever:
    """
    Snowflake Cortex Search retriever for RetailAssist.

    Retrieval flow:

    1. Detect the customer's policy category.
    2. Ask Cortex Search for a larger candidate set.
    3. Rank candidates using:
       - exact FAQ-question match
       - FAQ-question similarity
       - meaningful word overlap
       - Cortex relevance score
    4. Remove duplicate FAQ questions across documents.
    5. Return only the strongest evidence.

    This prevents cases such as:

        How long does delivery take?

    from returning:

        delivery.pdf
        delivery_faq_text.pdf
        shipping_faq.md

    when one document already contains the exact FAQ.
    """

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
            "refund has not arrived",
            "refund not arrived",
            "refund not received",
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
            "warranties",
            "guarantee",
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
        ],
    }

    STOP_WORDS = {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
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
        "this",
        "that",
        "these",
        "those",
        "what",
        "when",
        "where",
        "why",
        "how",
        "who",
        "which",
        "and",
        "or",
        "but",
        "if",
        "for",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "about",
        "as",
        "into",
        "through",
        "after",
        "before",
        "during",
        "than",
        "then",
        "it",
        "its",
        "have",
        "has",
        "had",
        "not",
        "please",
        "tell",
    }

    def __init__(
        self,
        session=None,
        search_service="RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH",
        top_k: int = 5,
    ):
        self.session = session or create_snowflake_session()
        self.search_service = search_service
        self.top_k = top_k

    # ============================================================
    # Text helpers
    # ============================================================

    @staticmethod
    def _normalize(text: str) -> str:
        """
        Normalize text for comparison.
        """
        if not text:
            return ""

        text = text.lower().strip()

        # Remove markdown heading markers.
        text = re.sub(r"^#{1,6}\s*", "", text)

        # Remove FAQ numbering such as:
        # 1.
        # 11.
        # Q1:
        # Q2.
        text = re.sub(
            r"^(?:q\s*)?\d+\s*[\.\):\-]\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(r"[^a-z0-9]+", " ", text)

        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _meaningful_words(cls, text: str) -> set[str]:
        words = cls._normalize(text).split()

        return {word for word in words if len(word) >= 3 and word not in cls.STOP_WORDS}

    @classmethod
    def _extract_faq_question(cls, chunk_text: str) -> str:
        """
        Extract the FAQ question from the beginning of a chunk.

        Supports:

            ## How long does delivery take?

        and:

            1. How long does delivery take?

        and:

            How long does delivery take?
        """

        if not chunk_text:
            return ""

        lines = [line.strip() for line in chunk_text.splitlines() if line.strip()]

        if not lines:
            return ""

        for line in lines[:3]:
            cleaned = re.sub(
                r"^#{1,6}\s*",
                "",
                line,
            ).strip()

            cleaned = re.sub(
                r"^(?:q\s*)?\d+\s*[\.\):\-]\s*",
                "",
                cleaned,
                flags=re.IGNORECASE,
            ).strip()

            if cleaned.endswith("?"):
                return cleaned

        return ""

    @classmethod
    def _faq_key(cls, chunk_text: str) -> str:
        """
        Stable key used to identify the same FAQ question across
        different documents and formats.
        """

        question = cls._extract_faq_question(chunk_text)

        if question:
            return cls._normalize(question)

        # Fallback to first meaningful line.
        lines = [line.strip() for line in chunk_text.splitlines() if line.strip()]

        if not lines:
            return ""

        return cls._normalize(lines[0])

    # ============================================================
    # Category detection
    # ============================================================

    @classmethod
    def _detect_category(cls, query: str) -> str | None:
        """
        Detect the strongest policy category.

        Multi-word phrases receive higher weight than
        generic single words.
        """

        text = query.lower().strip()

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

        best_category, best_score = ranked[0]

        if best_score == 0:
            return None

        second_score = ranked[1][1] if len(ranked) > 1 else 0

        if best_score > second_score:
            return best_category

        return None

    # ============================================================
    # Candidate scoring
    # ============================================================

    @classmethod
    def _candidate_score(
        cls,
        query: str,
        chunk: RetrievedChunk,
    ) -> float:
        """
        Calculate application-level relevance.

        Exact FAQ question matches receive a very large bonus.
        This is what makes an exact FAQ beat similar FAQs from
        other files.
        """

        query_normalized = cls._normalize(query)

        faq_question = cls._extract_faq_question(chunk.chunk_text)

        faq_normalized = cls._normalize(faq_question)

        query_words = cls._meaningful_words(query)

        faq_words = cls._meaningful_words(faq_question)

        chunk_words = cls._meaningful_words(chunk.chunk_text)

        score = 0.0

        # --------------------------------------------------------
        # 1. Exact FAQ question
        # --------------------------------------------------------

        if query_normalized and faq_normalized and query_normalized == faq_normalized:
            score += 100.0

        # --------------------------------------------------------
        # 2. FAQ question containment
        # --------------------------------------------------------

        if (
            query_normalized
            and faq_normalized
            and (
                query_normalized in faq_normalized or faq_normalized in query_normalized
            )
        ):
            score += 25.0

        # --------------------------------------------------------
        # 3. Meaningful word overlap
        # --------------------------------------------------------

        faq_overlap = query_words.intersection(faq_words)

        chunk_overlap = query_words.intersection(chunk_words)

        score += len(faq_overlap) * 6.0
        score += len(chunk_overlap) * 1.5

        # --------------------------------------------------------
        # 4. Phrase matching
        # --------------------------------------------------------

        query_words_list = cls._normalize(query).split()

        faq_words_list = cls._normalize(faq_question).split()

        for phrase_size in (4, 3, 2):
            if len(query_words_list) < phrase_size:
                continue

            for index in range(len(query_words_list) - phrase_size + 1):
                phrase = " ".join(query_words_list[index : index + phrase_size])

                if phrase and phrase in faq_normalized:
                    score += 8.0

        # --------------------------------------------------------
        # 5. Cortex score as a tie breaker
        # --------------------------------------------------------

        if chunk.score is not None:
            try:
                score += float(chunk.score) * 5.0
            except (TypeError, ValueError):
                pass

        return score

    # ============================================================
    # Retrieval
    # ============================================================

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:

        if not query or not query.strip():
            raise ValueError("Query cannot be empty.")

        limit = top_k if top_k is not None else self.top_k

        category = self._detect_category(query)

        # Ask Cortex for more candidates than we finally expose.
        # This gives our ranking layer enough candidates to find
        # the exact FAQ.
        candidate_limit = max(
            20,
            limit * 5,
        )

        search_request = {
            "query": query.strip(),
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT",
            ],
            "limit": candidate_limit,
        }

        # --------------------------------------------------------
        # Category filter
        # --------------------------------------------------------

        if category:
            search_request["filter"] = {
                "@eq": {
                    "CATEGORY": category,
                }
            }

        request_json = json.dumps(search_request)

        sql = f"""
        SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
            '{self.search_service}',
            $$ {request_json} $$
        ) AS SEARCH_RESULT
        """

        rows = self.session.sql(sql).collect()

        if not rows:
            return []

        raw_result = rows[0]["SEARCH_RESULT"]

        if isinstance(raw_result, str):
            result = json.loads(raw_result)
        else:
            result = raw_result

        results = result.get(
            "results",
            [],
        )

        candidates = []

        # --------------------------------------------------------
        # Convert Cortex results
        # --------------------------------------------------------

        for item in results:
            chunk_text = str(
                item.get(
                    "CHUNK_TEXT",
                    "",
                )
            ).strip()

            if not chunk_text:
                continue

            try:
                chunk_index = int(
                    item.get(
                        "CHUNK_INDEX",
                        0,
                    )
                )
            except (
                TypeError,
                ValueError,
            ):
                chunk_index = 0

            cortex_score = None

            if item.get("score") is not None:
                try:
                    cortex_score = float(item["score"])
                except (
                    TypeError,
                    ValueError,
                ):
                    cortex_score = None

            chunk = RetrievedChunk(
                chunk_id=str(
                    item.get(
                        "CHUNK_ID",
                        "",
                    )
                ),
                document_id=str(
                    item.get(
                        "DOCUMENT_ID",
                        "",
                    )
                ),
                document_name=str(
                    item.get(
                        "DOCUMENT_NAME",
                        "",
                    )
                ),
                category=str(
                    item.get(
                        "CATEGORY",
                        "",
                    )
                )
                .strip()
                .lower(),
                chunk_index=chunk_index,
                chunk_text=chunk_text,
                score=cortex_score,
            )

            # Defense-in-depth category filter.
            if category and chunk.category and chunk.category != category:
                continue

            relevance = self._candidate_score(
                query,
                chunk,
            )

            candidates.append(
                (
                    relevance,
                    chunk,
                )
            )

        if not candidates:
            return []

        # --------------------------------------------------------
        # Sort strongest candidates first
        # --------------------------------------------------------

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        # --------------------------------------------------------
        # Exact FAQ match
        #
        # If the user asks exactly the FAQ question,
        # return ONE source only.
        # --------------------------------------------------------

        exact_candidates = [
            pair
            for pair in candidates
            if self._normalize(query)
            == self._normalize(self._extract_faq_question(pair[1].chunk_text))
        ]

        if exact_candidates:
            return [exact_candidates[0][1]]

        # --------------------------------------------------------
        # Remove duplicate FAQ questions
        #
        # Example:
        #
        # payments_faq.md
        # payment_faq_final_1_.docx
        #
        # both contain:
        #
        # "Why was my card declined?"
        #
        # Only the strongest one survives.
        # --------------------------------------------------------

        unique_faqs = {}

        for relevance, chunk in candidates:
            faq_key = self._faq_key(chunk.chunk_text)

            if not faq_key:
                faq_key = f"{chunk.document_id}:{chunk.chunk_id}"

            if faq_key not in unique_faqs:
                unique_faqs[faq_key] = (
                    relevance,
                    chunk,
                )

        unique_candidates = list(unique_faqs.values())

        unique_candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        if not unique_candidates:
            return []

        # --------------------------------------------------------
        # Relevance gate
        #
        # Do not expose weak Cortex candidates merely because
        # they belong to the same category.
        # --------------------------------------------------------

        best_score = unique_candidates[0][0]

        selected = []

        for relevance, chunk in unique_candidates:
            if len(selected) >= limit:
                break

            # Keep candidates close to the strongest candidate.
            if best_score > 0:
                if relevance < best_score * 0.45:
                    continue

            selected.append(chunk)

        return selected
