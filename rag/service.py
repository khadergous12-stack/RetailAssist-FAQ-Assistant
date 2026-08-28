from __future__ import annotations

from dataclasses import dataclass
import re
from difflib import SequenceMatcher

from rag.contracts import Generator, RetrievedChunk, Retriever
from rag.prompts import REFUSAL_MESSAGE, SYSTEM_PROMPT, build_grounded_prompt


@dataclass
class RAGResponse:
    """Final response returned by the RAG service."""

    answer: str
    evidence: list[RetrievedChunk]


class RAGService:
    """
    Provider-neutral grounded RAG orchestration.

    IMPORTANT DESIGN CHANGE
    -----------------------
    Cortex Search returns *candidates*, not final evidence.

    This class makes the final evidence decision.  A chunk is displayed only
    when it matches the customer's actual topic AND the kind of answer being
    requested.  This prevents a document with several FAQ entries from
    leaking unrelated chunks into the UI.

    Examples:
      - "standard shipping" does not keep the express-shipping chunk.
      - a tracking question does not keep a standard-shipping chunk merely
        because both mention delivery.
      - "package arrives damaged" does not keep "who pays for return
        shipping" just because both mention damage.
    """

    MAX_EVIDENCE = 3
    MIN_SCORE = 5.0
    RELATIVE_THRESHOLD = 0.62

    # Mutually exclusive variants. If a customer explicitly asks for one
    # variant, evidence for another variant must not be shown.
    VARIANT_GROUPS = (
        {"standard", "express", "overnight", "priority"},
        {"monthly", "annual", "yearly"},
    )

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
        "must",
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
        "whom",
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
        "policy",
        "policies",
        "something",
        "thing",
        "exist",
        "exists",
        "uploaded",
        "document",
        "documents",
        "answer",
        "answers",
        "question",
        "questions",
        "normally",
        "customer",
        "customers",
        "retailassist",
        "supportai",
    }

    IRREGULAR_FORMS = {
        "delivery": "deliver",
        "deliveries": "deliver",
        "delivered": "deliver",
        "shipping": "ship",
        "shipments": "shipment",
        "shipped": "ship",
        "returned": "return",
        "returns": "return",
        "returning": "return",
        "refunded": "refund",
        "refunds": "refund",
        "payments": "payment",
        "charged": "charge",
        "charges": "charge",
        "damaged": "damage",
        "damages": "damage",
        "broken": "break",
        "cracked": "crack",
        "defective": "defect",
        "products": "product",
        "packages": "package",
        "orders": "order",
        "addresses": "address",
        "changes": "change",
        "changed": "change",
        "changing": "change",
        "takes": "take",
        "took": "take",
        "arrived": "arrive",
        "arrives": "arrive",
        "arriving": "arrive",
        "missing": "miss",
        "lost": "lose",
        "pays": "pay",
        "paid": "pay",
        "fees": "fee",
    }

    # These are intent labels, not document names or policy rules.  They are
    # deliberately generic so the solution works with future documents.
    INTENT_PATTERNS = {
        "duration": (
            r"\bhow long\b",
            r"\bhow many days\b",
            r"\bdelivery time\b",
            r"\btime does\b",
            r"\btake\b",
            r"\bwithin\s+\d+\b",
        ),
        "tracking": (
            r"\btracking\b",
            r"\btracking number\b",
            r"\btracking shows delivered\b",
            r"\btrack(?:ing)? (?:the )?(?:order|shipment|package)\b",
            r"\blocate\b",
            r"\bmissing\b",
            r"\bnot updated\b",
            r"\bshipment status\b",
        ),
        "damage": (
            r"\bdamage\w*\b",
            r"\bbroken\b",
            r"\bcrack\w*\b",
            r"\bleak\w*\b",
            r"\bdefect\w*\b",
            r"\bunusable\b",
        ),
        "return": (
            r"\breturn\w*\b",
            r"\bsend(?:ing)?\s+(?:it|the product|the item)?\s*back\b",
        ),
        "cost": (
            r"\bcost\w*\b",
            r"\bfee\w*\b",
            r"\bpay(?:s|ing|ment)?\b",
            r"\bcharge\w*\b",
            r"\bwho pays\b",
        ),
        "refund": (
            r"\brefund\w*\b",
            r"\bcredit\b",
            r"\breimburse\w*\b",
        ),
        "procedure": (
            r"\bwhat should i do\b",
            r"\bwhat do i do\b",
            r"\bhow do i\b",
            r"\bwhat can i do\b",
            r"\bsteps?\b",
            r"\bprocess\b",
            r"\bcontact support\b",
        ),
        "eligibility": (
            r"\bcan i\b",
            r"\bam i eligible\b",
            r"\beligib\w*\b",
            r"\bcovered\b",
            r"\ballowed\b",
        ),
    }

    def __init__(self, retriever: Retriever, generator: Generator):
        self.retriever = retriever
        self.generator = generator

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def answer(self, question: str, top_k: int = 5) -> RAGResponse:
        if not question or not question.strip():
            raise ValueError("Question cannot be empty.")

        question = question.strip()
        retrieved = self.retriever.retrieve(query=question, top_k=top_k)

        if not retrieved:
            return self._refusal_response()

        evidence = self._filter_evidence(question, retrieved)
        if not evidence:
            return self._refusal_response()

        evidence_text = self._format_evidence(evidence)
        prompt = f"{SYSTEM_PROMPT}\n\n{build_grounded_prompt(question, evidence_text)}"
        answer = self.generator.generate(prompt)

        if self._is_refusal(answer):
            return self._refusal_response()

        return RAGResponse(answer=answer.strip(), evidence=evidence)

    @staticmethod
    def _refusal_response() -> RAGResponse:
        return RAGResponse(answer=REFUSAL_MESSAGE, evidence=[])

    @staticmethod
    def _is_refusal(answer: str) -> bool:
        if not answer:
            return True
        normalized = re.sub(r"\s+", " ", answer.lower().strip())
        return any(
            phrase in normalized
            for phrase in (
                "i couldn't find a policy",
                "i could not find a policy",
                "couldn't find a policy",
                "could not find a policy",
                "not enough information",
                "no policy was found",
            )
        )

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    @classmethod
    def _normalize_word(cls, word: str) -> str:
        word = word.lower().strip()
        if not word:
            return ""
        if word in cls.IRREGULAR_FORMS:
            return cls.IRREGULAR_FORMS[word]
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
        # Conservative singularization. Do not corrupt words such as
        # "express", "business", "address", etc.
        if (
            word.endswith("s")
            and len(word) > 4
            and not word.endswith(("ss", "us", "is"))
        ):
            return word[:-1]
        return word

    @classmethod
    def _tokens(cls, text: str) -> list[str]:
        result = []
        for raw in re.findall(r"[A-Za-z0-9]+", str(text or "").lower()):
            if len(raw) < 3 or raw in cls.STOP_WORDS:
                continue
            word = cls._normalize_word(raw)
            if len(word) >= 3 and word not in cls.STOP_WORDS:
                result.append(word)
        return result

    @classmethod
    def _words(cls, text: str) -> set[str]:
        return set(cls._tokens(text))

    @classmethod
    def _extract_question(cls, text: str) -> str:
        """Extract an FAQ question when the chunk contains one."""
        if not text:
            return ""
        lines = [x.strip() for x in str(text).splitlines() if x.strip()]
        for line in lines[:8]:
            cleaned = re.sub(r"^#{1,6}\s*", "", line).strip()
            cleaned = re.sub(
                r"^(?:q\s*)?\d+\s*[\.\):\-]\s*",
                "",
                cleaned,
                flags=re.IGNORECASE,
            ).strip()
            if "?" in cleaned:
                return cleaned.split("?", 1)[0].strip() + "?"
        return ""

    @classmethod
    def _candidate_label(cls, chunk: RetrievedChunk) -> str:
        """Text used to identify what a non-FAQ policy chunk is about."""
        question = cls._extract_question(chunk.chunk_text)
        if question:
            return question
        if chunk.section_heading:
            return chunk.section_heading
        lines = [
            x.strip() for x in str(chunk.chunk_text or "").splitlines() if x.strip()
        ]
        return " ".join(lines[:2])

    # ------------------------------------------------------------------
    # Intent detection
    # ------------------------------------------------------------------

    @classmethod
    def _intents(cls, text: str) -> set[str]:
        value = str(text or "").lower()
        return {
            intent
            for intent, patterns in cls.INTENT_PATTERNS.items()
            if any(re.search(pattern, value) for pattern in patterns)
        }

    @classmethod
    def _candidate_intents(cls, chunk: RetrievedChunk) -> set[str]:
        question = cls._extract_question(chunk.chunk_text)
        if question:
            return cls._intents(question)

        # Uploaded policy sections often contain several sentences.  The
        # section heading should carry most of the intent signal; then add
        # only explicit answer-type phrases from the body.  This prevents a
        # standard-shipping section from becoming a "tracking" section just
        # because it says the order is "delivered".
        heading = chunk.section_heading or ""
        intents = cls._intents(heading)
        body = str(chunk.chunk_text or "").lower()

        if re.search(
            r"\bwithin\s+\d+(?:\s*[–-]\s*\d+)?\s+(?:business\s+)?days?\b", body
        ):
            intents.add("duration")
        elif re.search(
            r"\b\d+(?:\s*[–-]\s*\d+)?\s+(?:business\s+)?days?\s+after\b", body
        ):
            intents.add("duration")
        if re.search(
            r"\btracking\b|\btracking number\b|tracking shows delivered|\btrack the order\b",
            body,
        ):
            intents.add("tracking")
        if re.search(
            r"\bdamage\w*\b|\bbroken\b|\bcrack\w*\b|\bleak\w*\b|\bdefect\w*\b|\bunusable\b",
            body,
        ):
            intents.add("damage")
        if re.search(
            r"\breturn\w*\b|\bsend(?:ing)?\s+(?:it|the product|the item)?\s*back\b",
            body,
        ):
            intents.add("return")
        if re.search(
            r"\bkeep the packaging\b|\bcontact support\b|\bwhat should i do\b|\bhow do i\b",
            body,
        ):
            intents.add("procedure")
        if re.search(r"\bcost\w*\b|\bfee\w*\b|\bwho pays\b|\bshipping cost\b", body):
            intents.add("cost")
        if re.search(r"\brefund\w*\b|\bcredit\b", body):
            intents.add("refund")
        return intents

    @classmethod
    def _intent_compatibility(
        cls,
        query_intents: set[str],
        candidate_intents: set[str],
        query_words: set[str],
        candidate_words: set[str],
    ) -> float:
        if not query_intents:
            return 0.0

        overlap = query_intents & candidate_intents
        score = len(overlap) * 5.0

        # A "procedure" question can legitimately be answered by an
        # eligibility/return section when the same concrete issue is present.
        if "procedure" in query_intents and candidate_intents & {
            "return",
            "tracking",
            "damage",
            "refund",
            "eligibility",
        }:
            if query_words & candidate_words:
                score += 2.5

        # A cost question should not be accepted as evidence for a procedure
        # question unless the candidate also contains a procedure intent.
        if "cost" in query_intents and "cost" not in candidate_intents:
            score -= 3.0
        if (
            "procedure" in query_intents
            and "cost" in candidate_intents
            and "procedure" not in candidate_intents
        ):
            score -= 7.0

        # Duration, tracking, refund and cost are distinct answer types.
        exclusive = ("duration", "tracking", "refund", "cost")
        for intent in exclusive:
            if intent in query_intents and intent not in candidate_intents:
                score -= 4.0

        return score

    # ------------------------------------------------------------------
    # Source identity
    # ------------------------------------------------------------------

    @staticmethod
    def _is_uploaded(chunk: RetrievedChunk) -> bool:
        return str(chunk.source_type or "").strip().upper() == "USER_UPLOAD"

    # ------------------------------------------------------------------
    # Relevance scoring
    # ------------------------------------------------------------------

    @classmethod
    def _score_chunk(
        cls,
        question: str,
        chunk: RetrievedChunk,
        position: int,
    ) -> float:
        query_words = cls._words(question)
        if not query_words:
            return 0.0

        faq_question = cls._extract_question(chunk.chunk_text)
        label = cls._candidate_label(chunk)
        body_words = cls._words(chunk.chunk_text)
        label_words = cls._words(label)
        context_words = cls._words(
            " ".join([chunk.section_heading or "", chunk.category or ""])
        )

        label_overlap = query_words & label_words
        body_overlap = query_words & body_words
        context_overlap = query_words & context_words

        score = 0.0

        # Explicit variant mismatch is a hard semantic distinction.
        # Example: an express question must not fall back to a standard
        # delivery FAQ simply because both contain "delivery" and "take".
        for group in cls.VARIANT_GROUPS:
            query_variants = query_words & group
            if query_variants:
                candidate_variants = body_words & group
                if not (query_variants & candidate_variants):
                    score -= 18.0

        # The FAQ question/section heading is the best indicator of what a
        # chunk is actually answering.
        score += len(label_overlap) * 7.0
        score += len(body_overlap) * 1.5
        score += len(context_overlap) * 0.5

        if faq_question:
            q_tokens = cls._tokens(question)
            f_tokens = cls._tokens(faq_question)
            if q_tokens and f_tokens:
                score += (
                    SequenceMatcher(
                        None,
                        " ".join(q_tokens),
                        " ".join(f_tokens),
                    ).ratio()
                    * 7.0
                )

        query_intents = cls._intents(question)
        candidate_intents = cls._candidate_intents(chunk)
        score += cls._intent_compatibility(
            query_intents,
            candidate_intents,
            query_words,
            body_words,
        )

        # Preserve highly distinctive query terms such as standard/express,
        # tracking/delivered, refund, etc.  A candidate missing such a term is
        # much less likely to be the answer even if generic words overlap.
        distinctive = {
            "standard",
            "express",
            "international",
            "overnight",
            "track",
            "tracking",
            "deliver",
            "package",
            "damage",
            "break",
            "crack",
            "refund",
            "return",
            "payment",
            "charge",
            "cost",
            "pay",
            "warranty",
            "accident",
            "cancel",
            "cancellation",
        }
        query_distinctive = query_words & distinctive
        if query_distinctive:
            coverage = len(query_distinctive & body_words) / len(query_distinctive)
            score += coverage * 6.0
            missing = query_distinctive - body_words
            score -= len(missing) * 1.5

        # A procedure question needs an actionable/procedural section.  A
        # "who pays" FAQ can mention the same damaged item but does not answer
        # "what should I do".  Only relax this when the customer explicitly
        # asks about returning/sending the item back.
        if "procedure" in query_intents:
            if "procedure" in candidate_intents:
                score += 6.0
            elif "return" in candidate_intents and "return" in query_words:
                score += 1.0
            else:
                score -= 6.0

        # Cortex score is a tie-breaker, never the final evidence decision.
        if chunk.score is not None:
            try:
                score += max(float(chunk.score), 0.0) * 0.75
            except (TypeError, ValueError):
                pass

        score += max(0.0, 0.15 - position * 0.01)
        return score

    @classmethod
    def _filter_evidence(
        cls,
        question: str,
        chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """
        Select the strongest evidence for the customer's question.

        Retrieval gives us a broad candidate set. This method applies
        lightweight semantic filtering without being so aggressive that
        correct FAQ chunks are discarded.
        """
        valid = [chunk for chunk in chunks if str(chunk.chunk_text or "").strip()]

        if not valid:
            return []

        query_words = cls._words(question)
        query_intents = cls._intents(question)

        scored = []

        for position, chunk in enumerate(valid):
            score = cls._score_chunk(
                question=question,
                chunk=chunk,
                position=position,
            )

            candidate_words = cls._words(chunk.chunk_text)
            candidate_intents = cls._candidate_intents(chunk)

            # ----------------------------------------------------------
            # Variant protection
            # ----------------------------------------------------------
            # If the customer explicitly asks for one shipping variant,
            # don't use another variant as the answer.
            variant_mismatch = False

            for group in cls.VARIANT_GROUPS:
                query_variants = query_words & group
                candidate_variants = candidate_words & group

                if query_variants and not (query_variants & candidate_variants):
                    variant_mismatch = True
                    break

            if variant_mismatch:
                continue

            # ----------------------------------------------------------
            # Strong intent protection
            # ----------------------------------------------------------
            # These intents represent substantially different answer types.
            # Only enforce the gate when the query actually contains one.
            exclusive_intents = {
                "duration",
                "tracking",
                "refund",
                "cost",
            }

            required_intents = query_intents & exclusive_intents

            if required_intents:
                if not (required_intents & candidate_intents):
                    continue

            # ----------------------------------------------------------
            # Damage / return protection
            # ----------------------------------------------------------
            # A question specifically about damage should be supported by
            # damage-related evidence.
            if "damage" in query_intents:
                if "damage" not in candidate_intents:
                    continue

            # A question specifically about returning something should
            # normally use return-related evidence.
            if "return" in query_intents:
                if "return" not in candidate_intents:
                    continue

            # ----------------------------------------------------------
            # Procedure protection
            # ----------------------------------------------------------
            # Procedure questions need actionable evidence. However, do not
            # reject a useful FAQ simply because it does not contain an
            # explicit "procedure" label when the concrete operation matches.
            if "procedure" in query_intents:
                concrete_intents = {
                    "return",
                    "tracking",
                    "damage",
                    "refund",
                    "eligibility",
                }

                if not (
                    "procedure" in candidate_intents
                    or (
                        candidate_intents & concrete_intents
                        and query_words & candidate_words
                    )
                ):
                    continue

            scored.append((score, position, chunk))

        # --------------------------------------------------------------
        # Fallback
        # --------------------------------------------------------------
        # If semantic gates removed everything, don't immediately refuse.
        # The retriever may have found valid evidence that the lightweight
        # intent classifier failed to recognize.
        if not scored:
            rescored = []

            for position, chunk in enumerate(valid):
                score = cls._score_chunk(
                    question=question,
                    chunk=chunk,
                    position=position,
                )

                candidate_words = cls._words(chunk.chunk_text)

                # Still protect explicit variants in the fallback.
                variant_mismatch = False

                for group in cls.VARIANT_GROUPS:
                    query_variants = query_words & group
                    candidate_variants = candidate_words & group

                    if query_variants and not (query_variants & candidate_variants):
                        variant_mismatch = True
                        break

                if variant_mismatch:
                    continue

                rescored.append((score, position, chunk))

            if not rescored:
                return []

            rescored.sort(
                key=lambda item: item[0],
                reverse=True,
            )

            best_score, _, best_chunk = rescored[0]

            # Require at least some lexical evidence in the fallback.
            best_words = cls._words(best_chunk.chunk_text)

            if query_words and not (query_words & best_words):
                return []

            return [best_chunk]

        # --------------------------------------------------------------
        # Sort strongest evidence first
        # --------------------------------------------------------------
        scored.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        best_score, _, best_chunk = scored[0]

        # --------------------------------------------------------------
        # Evidence quality threshold
        # --------------------------------------------------------------
        # The score produced by _score_chunk combines lexical overlap,
        # FAQ-question similarity, intent compatibility and the Snowflake
        # retrieval score.
        #
        # Do not use an unnecessarily high threshold here. A correct FAQ
        # question can have a relatively modest score while still being
        # clearly relevant.
        if best_score < 2.0:
            return []

        # --------------------------------------------------------------
        # Keep a small amount of supporting evidence
        # --------------------------------------------------------------
        selected = [best_chunk]

        # Add closely related supporting chunks only when they are genuinely
        # close to the best result. This prevents unrelated FAQ sections from
        # being passed to the generator.
        for score, _, chunk in scored[1:]:
            if len(selected) >= 3:
                break

            if score < best_score - 5.0:
                continue

            selected.append(chunk)

        return selected

    # ------------------------------------------------------------------
    # Prompt formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_evidence(chunks: list[RetrievedChunk]) -> str:
        formatted = []
        for index, chunk in enumerate(chunks, start=1):
            page = str(chunk.page_number) if chunk.page_number is not None else "N/A"
            section = chunk.section_heading or "N/A"
            formatted.append(
                f"""Evidence {index}

Document: {chunk.document_name}
Document ID: {chunk.document_id}
Category: {chunk.category or "Uncategorized"}
Chunk ID: {chunk.chunk_id}
Chunk Index: {chunk.chunk_index}
Page Number: {page}
Section: {section}

Text:
{chunk.chunk_text}""".strip()
            )
        return "\n\n".join(formatted)
