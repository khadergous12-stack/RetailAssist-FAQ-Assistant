from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import time
from difflib import SequenceMatcher

from rag.contracts import Generator, RetrievedChunk, Retriever
from rag.prompts import REFUSAL_MESSAGE, SYSTEM_PROMPT, build_grounded_prompt


logger = logging.getLogger(__name__)


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
        {"standard", "express", "expedited", "overnight", "priority"},
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
        "warranty": (
            r"\bwarranty\b",
            r"\bguarantee\b",
        ),
        "replacement": (
            r"\breplacement\b",
            r"\breplace\w*\b",
        ),
        "address_change": (
            r"\bshipping address\b",
            r"\bchange(?:d|s|ing)?\b.*\baddress\b",
            r"\baddress\b.*\bchange(?:d|s|ing)?\b",
            r"\bredirect\w*\b.*\bparcel\b",
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
        "limit": (
            r"\bmaximum\b",
            r"\bminimum\b",
            r"\blimit\b",
            r"\blimits\b",
            r"\bmax(?:imum)?\b",
            r"\bhow large\b",
            r"\bhow many files\b",
            r"\bfile size\b",
            r"\bsize limit\b",
        ),
    }

    def __init__(self, retriever: Retriever, generator: Generator):
        self.retriever = retriever
        self.generator = generator

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def answer(self, question: str, top_k: int = 5) -> RAGResponse:
        """
        Execute the complete grounded RAG workflow with diagnostic logging.

        Logging records timing and pipeline state so slow or failed requests
        can be traced to retrieval, evidence filtering, or generation.
        Customer question text and prompt contents are intentionally not logged.
        """
        if not question or not question.strip():
            logger.warning("RAG request rejected: question is empty.")
            raise ValueError("Question cannot be empty.")

        question = question.strip()
        pipeline_start = time.perf_counter()

        logger.info(
            "RAG request started | top_k=%s | question_length=%s",
            top_k,
            len(question),
        )

        # --------------------------------------------------------------
        # Retrieval
        # --------------------------------------------------------------
        retrieval_start = time.perf_counter()
        logger.info("RAG retrieval started.")

        try:
            retrieved = self.retriever.retrieve(
                query=question,
                top_k=top_k,
            )

            # Targeted query expansion for ambiguous cross-domain wording.
            # This does not change normal retrieval; it only asks Cortex Search
            # for one additional candidate set when the question clearly
            # belongs to a specific policy family.
            query_lower = question.lower()
            supplemental_query = None

            if (
                "refund" in query_lower
                and "fee" in query_lower
                and ("express" in query_lower or "expedited" in query_lower)
            ):
                supplemental_query = f"{question} expedited shipping upgrade refund delivery fee refund policy"
            elif "shipping address" in query_lower or (
                "address" in query_lower
                and (
                    "parcel" in query_lower
                    or "truck" in query_lower
                    or "shipment" in query_lower
                )
            ):
                supplemental_query = f"{question} change shipping address after dispatch parcel truck shipment"

            if supplemental_query:
                try:
                    supplemental = self.retriever.retrieve(
                        query=supplemental_query,
                        top_k=max(top_k, 8),
                    )
                    initial_count = len(retrieved)
                    seen = {
                        (
                            str(chunk.document_id),
                            str(chunk.chunk_id),
                            str(chunk.chunk_index),
                        )
                        for chunk in retrieved
                    }
                    for chunk in supplemental:
                        key = (
                            str(chunk.document_id),
                            str(chunk.chunk_id),
                            str(chunk.chunk_index),
                        )
                        if key not in seen:
                            retrieved.append(chunk)
                            seen.add(key)
                    logger.info(
                        "Supplemental retrieval completed | added=%s",
                        len(retrieved) - initial_count,
                    )
                except Exception:
                    logger.exception(
                        "Supplemental retrieval failed; continuing with primary results."
                    )

        except Exception:
            retrieval_elapsed = time.perf_counter() - retrieval_start
            logger.exception(
                "RAG retrieval failed | duration=%.3fs",
                retrieval_elapsed,
            )
            raise

        retrieval_elapsed = time.perf_counter() - retrieval_start

        logger.info(
            "RAG retrieval completed | candidates=%s | duration=%.3fs",
            len(retrieved),
            retrieval_elapsed,
        )

        if retrieval_elapsed >= 10.0:
            logger.warning(
                "Slow retrieval detected | duration=%.3fs",
                retrieval_elapsed,
            )

        if not retrieved:
            logger.warning("RAG retrieval returned no candidates; returning refusal.")
            total_elapsed = time.perf_counter() - pipeline_start
            logger.info(
                "RAG request completed with refusal | duration=%.3fs",
                total_elapsed,
            )
            return self._refusal_response()

        # --------------------------------------------------------------
        # Evidence filtering
        # --------------------------------------------------------------
        filtering_start = time.perf_counter()

        logger.info(
            "Evidence filtering started | candidates=%s",
            len(retrieved),
        )

        try:
            evidence = self._filter_evidence(question, retrieved)
        except Exception:
            filtering_elapsed = time.perf_counter() - filtering_start
            logger.exception(
                "Evidence filtering failed | duration=%.3fs",
                filtering_elapsed,
            )
            raise

        filtering_elapsed = time.perf_counter() - filtering_start

        logger.info(
            "Evidence filtering completed | accepted=%s | duration=%.3fs",
            len(evidence),
            filtering_elapsed,
        )

        if not evidence:
            logger.warning(
                "No relevant evidence remained after filtering; returning refusal."
            )
            total_elapsed = time.perf_counter() - pipeline_start
            logger.info(
                "RAG request completed with refusal | duration=%.3fs",
                total_elapsed,
            )
            return self._refusal_response()

        # --------------------------------------------------------------
        # Prompt construction
        # --------------------------------------------------------------
        prompt_start = time.perf_counter()

        logger.info(
            "Grounded prompt construction started | evidence=%s",
            len(evidence),
        )

        try:
            evidence_text = self._format_evidence(evidence)
            prompt = (
                f"{SYSTEM_PROMPT}\n\n{build_grounded_prompt(question, evidence_text)}"
            )
        except Exception:
            prompt_elapsed = time.perf_counter() - prompt_start
            logger.exception(
                "Prompt construction failed | duration=%.3fs",
                prompt_elapsed,
            )
            raise

        prompt_elapsed = time.perf_counter() - prompt_start

        logger.info(
            "Grounded prompt construction completed | prompt_length=%s | duration=%.3fs",
            len(prompt),
            prompt_elapsed,
        )

        # --------------------------------------------------------------
        # Generation
        # --------------------------------------------------------------
        generation_start = time.perf_counter()
        logger.info(
            "Answer generation started | evidence=%s",
            len(evidence),
        )

        try:
            answer = self.generator.generate(prompt)
        except Exception:
            generation_elapsed = time.perf_counter() - generation_start
            logger.exception(
                "Answer generation failed | duration=%.3fs",
                generation_elapsed,
            )
            raise

        generation_elapsed = time.perf_counter() - generation_start

        logger.info(
            "Answer generation completed | response_length=%s | duration=%.3fs",
            len(answer or ""),
            generation_elapsed,
        )

        if generation_elapsed >= 10.0:
            logger.warning(
                "Slow generation detected | duration=%.3fs",
                generation_elapsed,
            )

        if self._is_refusal(answer):
            total_elapsed = time.perf_counter() - pipeline_start
            logger.info(
                "Generator returned a refusal | total_duration=%.3fs",
                total_elapsed,
            )
            return self._refusal_response()

        total_elapsed = time.perf_counter() - pipeline_start

        if total_elapsed >= 10.0:
            logger.warning(
                "Slow RAG request detected | total_duration=%.3fs",
                total_elapsed,
            )

        logger.info(
            "RAG request completed successfully | total_duration=%.3fs | evidence=%s",
            total_elapsed,
            len(evidence),
        )

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

        # Uploaded policy sections can contain several sentences.
        # Use the heading first, then add explicit answer-type signals
        # from the body.
        heading = chunk.section_heading or ""
        intents = cls._intents(heading)
        body = str(chunk.chunk_text or "").lower()

        if re.search(
            r"\bwithin\s+\d+(?:\s*[–-]\s*\d+)?\s+(?:business\s+)?days?\b",
            body,
        ):
            intents.add("duration")
        elif re.search(
            r"\b\d+(?:\s*[–-]\s*\d+)?\s+(?:business\s+)?days?\s+after\b",
            body,
        ):
            intents.add("duration")

        if re.search(
            r"\btracking\b"
            r"|\btracking number\b"
            r"|tracking shows delivered"
            r"|\btrack the order\b",
            body,
        ):
            intents.add("tracking")

        if re.search(
            r"\bdamage\w*\b"
            r"|\bbroken\b"
            r"|\bcrack\w*\b"
            r"|\bleak\w*\b"
            r"|\bdefect\w*\b"
            r"|\bunusable\b",
            body,
        ):
            intents.add("damage")

        if re.search(
            r"\breturn\w*\b"
            r"|\bsend(?:ing)?\s+(?:it|the product|the item)?\s*back\b",
            body,
        ):
            intents.add("return")

        if re.search(
            r"\bkeep the packaging\b"
            r"|\bcontact support\b"
            r"|\bwhat should i do\b"
            r"|\bwhat do i do\b"
            r"|\bhow do i\b",
            body,
        ):
            intents.add("procedure")

        if re.search(
            r"\bcost\w*\b"
            r"|\bfee\w*\b"
            r"|\bwho pays\b"
            r"|\bshipping cost\b",
            body,
        ):
            intents.add("cost")

        if re.search(
            r"\brefund\w*\b"
            r"|\bcredit\b",
            body,
        ):
            intents.add("refund")

        if re.search(
            r"\bwarranty\b"
            r"|\bguarantee\b",
            body,
        ):
            intents.add("warranty")

        if re.search(
            r"\breplacement\b"
            r"|\breplace\w*\b",
            body,
        ):
            intents.add("replacement")

        if re.search(
            r"\bshipping address\b"
            r"|\baddress\b.*\bchange(?:d|s|ing)?\b"
            r"|\bchange(?:d|s|ing)?\b.*\baddress\b"
            r"|\bredirect\w*\b.*\bparcel\b",
            body,
        ):
            intents.add("address_change")

        # Upload/document limit questions.
        if re.search(
            r"\bmaximum file size\b"
            r"|\bfile size\b"
            r"|\bsize limit\b"
            r"|\bupload limit\b"
            r"|\bupload restrictions?\b"
            r"|\bmaximum\b"
            r"|\b\d+\s*mb\b"
            r"|\bfiles?\s+per\s+upload\b",
            body,
        ):
            intents.add("limit")

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
        exclusive = ("duration", "tracking", "refund", "cost", "limit")
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

    @classmethod
    def _source_hint_score(
        cls,
        question: str,
        chunk: RetrievedChunk,
    ) -> float:
        """Prefer the policy domain explicitly named by the customer.

        This is a ranking hint only. It does not fabricate evidence and does
        not bypass the existing lexical/intent gates.
        """

        question_lower = str(question or "").lower()
        source_text = " ".join(
            [
                str(chunk.document_name or ""),
                str(chunk.category or ""),
                str(chunk.section_heading or ""),
            ]
        ).lower()

        score = 0.0

        # Warranty questions should strongly prefer warranty-domain evidence.
        if "warranty" in question_lower or "guarantee" in question_lower:
            if re.search(r"\bwarranty\b|\bguarantee\b", source_text):
                score += 10.0

        # Replacement warranty questions should prefer warranty evidence, not
        # generic returns/refunds/shipping documents.
        if "replacement" in question_lower:
            if re.search(r"\bwarranty\b|\bguarantee\b", source_text):
                score += 6.0

        # Explicit shipping-address changes are shipping-domain questions.
        if (
            "shipping address" in question_lower
            or ("address" in question_lower and "parcel" in question_lower)
            or "on the truck" in question_lower
        ):
            if re.search(
                r"shipping|shipment|delivery|carrier|dispatch|parcel|address",
                source_text,
            ):
                score += 20.0
            elif re.search(r"warranty|refund|returns?|payments?", source_text):
                score -= 20.0

        # A refund question mentioning an express delivery fee is a refund
        # policy question. The source hint prevents a shipping policy chunk
        # from winning merely because it shares "express" and "fee".
        if (
            "refund" in question_lower
            and "fee" in question_lower
            and "express" in question_lower
        ):
            if re.search(r"refund|returns?|reimburse|credit", source_text):
                score += 22.0
            elif re.search(r"shipping|delivery", source_text):
                score -= 20.0

        return score

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

        # Prefer uploaded documents when they provide relevant evidence.
        # Built-in FAQs remain available as fallback.
        if cls._is_uploaded(chunk):
            score += 5.0

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

        # Prefer the policy domain explicitly named in the question.
        score += cls._source_hint_score(
            question=question,
            chunk=chunk,
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
            "size",
            "limit",
            "maximum",
            "upload",
            "file",
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

            # Warranty-specific questions should use warranty-domain evidence.
            if "warranty" in query_intents:
                candidate_source = " ".join(
                    [
                        str(chunk.document_name or ""),
                        str(chunk.category or ""),
                        str(chunk.section_heading or ""),
                    ]
                ).lower()

                if "warranty" not in candidate_intents and not re.search(
                    r"\bwarranty\b|\bguarantee\b",
                    candidate_source,
                ):
                    continue

            # Replacement warranty questions should stay within warranty
            # evidence. The replacement intent alone is not enough because
            # return/refund documents can also mention replacement products.
            if "replacement" in query_intents and "warranty" in query_intents:
                candidate_source = " ".join(
                    [
                        str(chunk.document_name or ""),
                        str(chunk.category or ""),
                        str(chunk.section_heading or ""),
                    ]
                ).lower()

                if "warranty" not in candidate_intents and not re.search(
                    r"\bwarranty\b|\bguarantee\b",
                    candidate_source,
                ):
                    continue

            # Shipping-address changes are a distinct operational topic.
            # Prefer/require shipping-domain evidence for this specific query
            # so unrelated warranty or return chunks cannot win on generic
            # words such as "change", "parcel", or "address".
            if "address_change" in query_intents:
                candidate_source = " ".join(
                    [
                        str(chunk.document_name or ""),
                        str(chunk.category or ""),
                        str(chunk.section_heading or ""),
                    ]
                ).lower()

                shipping_domain = bool(
                    re.search(
                        r"shipping|shipment|delivery|carrier|dispatch|parcel|address",
                        candidate_source,
                    )
                )

                if "address_change" not in candidate_intents and not shipping_domain:
                    continue

            # A refund question that explicitly asks whether a delivery fee is
            # refunded belongs to the refund/returns domain. Do not allow a
            # shipping-domain chunk to win simply because it contains
            # "express" and "fee".
            if (
                "refund" in query_intents
                and "cost" in query_intents
                and "express" in query_words
            ):
                candidate_source = " ".join(
                    [
                        str(chunk.document_name or ""),
                        str(chunk.category or ""),
                        str(chunk.section_heading or ""),
                    ]
                ).lower()

                refund_domain = bool(
                    re.search(
                        r"refund|returns?|reimburse|credit",
                        candidate_source,
                    )
                )

                if "refund" not in candidate_intents and not refund_domain:
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
                    "warranty",
                    "replacement",
                    "address_change",
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

        # --------------------------------------------------------------
        # Explicit policy-domain selection
        # --------------------------------------------------------------
        # Some questions combine words that appear across multiple policies.
        # When the customer explicitly names the business outcome, prefer the
        # corresponding policy domain over a generic lexical match.
        question_lower = str(question or "").lower()

        def source_text(chunk: RetrievedChunk) -> str:
            # Domain selection must use SOURCE METADATA only.
            # Do not inspect chunk body text here because a shipping policy
            # can legitimately mention refunds, fees, addresses, etc.
            # Those words describe the policy rule, but they do not change
            # which policy family owns the evidence.
            return " ".join(
                [
                    str(chunk.document_name or ""),
                    str(chunk.category or ""),
                    str(chunk.section_heading or ""),
                ]
            ).lower()

        preferred_domain = None

        # "Express delivery fee refunded" is fundamentally a refund/charge
        # question. The refunds FAQ contains the explicit rule for expedited
        # shipping upgrades.
        if (
            "refund" in question_lower
            and "fee" in question_lower
            and ("express" in question_lower or "expedited" in question_lower)
        ):
            preferred_domain = "refund"

        # "Change shipping address" is fundamentally a shipping operation.
        elif "shipping address" in question_lower or (
            "address" in question_lower
            and (
                "parcel" in question_lower
                or "truck" in question_lower
                or "shipment" in question_lower
            )
        ):
            preferred_domain = "shipping"

        if preferred_domain:
            domain_candidates = []

            for item in scored:
                score, position, chunk = item
                candidate_source = source_text(chunk)

                if preferred_domain == "refund":
                    is_preferred = bool(
                        re.search(
                            r"refund|refunds_faq|refund policy|reimburse|credit|returns?",
                            candidate_source,
                        )
                    )
                else:
                    is_preferred = bool(
                        re.search(
                            r"shipping|shipment|delivery|carrier|dispatch|parcel|shipping_faq|shipping policy|address",
                            candidate_source,
                        )
                    )

                if is_preferred:
                    domain_candidates.append(item)

            if domain_candidates:
                # For an explicitly domain-specific question, do not let the
                # later compound-intent pass reintroduce evidence from another
                # policy family. Once a valid preferred-domain candidate exists,
                # restrict final evidence selection to that domain.
                domain_candidates.sort(
                    key=lambda item: item[0],
                    reverse=True,
                )
                scored = domain_candidates

        # Final semantic tie-break for the two known cross-domain ambiguities.
        # When the retrieved set contains the explicit policy rule, it wins over
        # a generic document that only shares words such as "express", "fee",
        # "parcel", or "address".
        if preferred_domain == "refund":
            explicit_refund = [
                item
                for item in scored
                if re.search(
                    r"expedited(?:-shipping)?(?:\s+shipping)?\s+(?:upgrade|upgrades).*?refund|shipping charges.*?refunded|express.*?fee.*?refund",
                    str(item[2].chunk_text or ""),
                    flags=re.IGNORECASE | re.DOTALL,
                )
            ]
            if explicit_refund:
                explicit_refund.sort(key=lambda item: item[0], reverse=True)
                scored = explicit_refund + [
                    item for item in scored if item not in explicit_refund
                ]
        elif preferred_domain == "shipping":
            explicit_shipping = [
                item
                for item in scored
                if re.search(
                    r"change.*?shipping address|shipping address.*?change|address.*?(?:already|truck|dispatch|parcel)",
                    str(item[2].chunk_text or ""),
                    flags=re.IGNORECASE | re.DOTALL,
                )
            ]
            if explicit_shipping:
                explicit_shipping.sort(key=lambda item: item[0], reverse=True)
                scored = explicit_shipping + [
                    item for item in scored if item not in explicit_shipping
                ]

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
        selected = []

        # --------------------------------------------------------------
        # Compound-intent evidence selection
        # --------------------------------------------------------------
        # For questions containing multiple answer requirements, preserve
        # evidence for different intents instead of selecting several chunks
        # that all answer the same aspect.
        #
        # Example:
        #   "What are the conditions for returning a product, and what steps
        #    do I need to follow?"
        #
        # should retain both:
        #   - return/eligibility evidence
        #   - procedure evidence
        # --------------------------------------------------------------

        def add_chunk(chunk):
            if chunk in selected:
                return
            if len(selected) < cls.MAX_EVIDENCE:
                selected.append(chunk)

        # First pass: satisfy explicit query intents.
        for intent in (
            "procedure",
            "eligibility",
            "return",
            "damage",
            "duration",
            "tracking",
            "refund",
            "cost",
            "warranty",
            "replacement",
            "address_change",
            "limit",
        ):
            if intent not in query_intents:
                continue

            for score, _, chunk in scored:
                candidate_intents = cls._candidate_intents(chunk)

                if intent in candidate_intents:
                    add_chunk(chunk)
                    break

            if len(selected) >= cls.MAX_EVIDENCE:
                break

        # Second pass: fill remaining slots with strongest evidence.
        if len(selected) < cls.MAX_EVIDENCE:
            for score, _, chunk in scored:
                if score < best_score - 5.0:
                    continue

                add_chunk(chunk)

                if len(selected) >= cls.MAX_EVIDENCE:
                    break

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
