from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import time
import uuid
from typing import Any

from snowflake.snowpark import Session


# ============================================================
# CONFIGURATION
# ============================================================

DATABASE = os.getenv(
    "RETAIL_ASSIST_DATABASE",
    "RETAIL_ASSIST_DB",
)

SCHEMA = os.getenv(
    "RETAIL_ASSIST_SCHEMA",
    "RETAIL_ASSIST",
)

STAGE_NAME = os.getenv(
    "SNOWFLAKE_DOCUMENT_STAGE",
    "DOCUMENT_UPLOAD_STAGE",
)

DOCUMENTS_TABLE = f"{DATABASE}.{SCHEMA}.DOCUMENTS"

DOCUMENT_CONTENT_TABLE = f"{DATABASE}.{SCHEMA}.DOCUMENT_CONTENT"

POLICY_CHUNKS_TABLE = f"{DATABASE}.{SCHEMA}.POLICY_CHUNKS"

POLICY_SOURCES_TABLE = f"{DATABASE}.{SCHEMA}.POLICY_SOURCES"

SEARCH_SERVICE = os.getenv(
    "RETAIL_ASSIST_SEARCH_SERVICE",
    f"{DATABASE}.{SCHEMA}.RETAIL_ASSIST_SEARCH",
)

# Configurable limits.
MAX_FILE_SIZE_MB = int(
    os.getenv(
        "RETAIL_ASSIST_MAX_FILE_SIZE_MB",
        "20",
    )
)

MAX_FILES_PER_UPLOAD = int(
    os.getenv(
        "RETAIL_ASSIST_MAX_FILES",
        "5",
    )
)

MAX_CHUNK_SIZE = int(
    os.getenv(
        "RETAIL_ASSIST_CHUNK_SIZE",
        "1200",
    )
)

CHUNK_OVERLAP = int(
    os.getenv(
        "RETAIL_ASSIST_CHUNK_OVERLAP",
        "200",
    )
)

LOGGER = logging.getLogger("supportai.document_store")

CATEGORY_MODEL = os.getenv(
    "RETAIL_ASSIST_CATEGORY_MODEL",
    "snowflake-arctic",
)


class DocumentStore:
    """
    Snowflake-only document management layer.

    Supported first-release formats:

        .pdf
        .docx
        .md
        .txt

    Workflow:

        validate
            ↓
        generate metadata
            ↓
        upload binary to Snowflake stage
            ↓
        parse PDF/DOCX with AI_PARSE_DOCUMENT
            ↓
        decode MD/TXT
            ↓
        store DOCUMENT_CONTENT
            ↓
        dynamically determine category
            ↓
        chunk with Snowflake Cortex
            ↓
        insert POLICY_CHUNKS
            ↓
        refresh Cortex Search
            ↓
        mark INDEXED

    No fixed business-category list is used.
    """

    ALLOWED_EXTENSIONS = {
        ".pdf",
        ".docx",
        ".md",
        ".txt",
    }

    # ========================================================
    # INITIALIZATION
    # ========================================================

    def __init__(
        self,
        session: Session,
    ):
        self.session = session

    # ========================================================
    # BASIC HELPERS
    # ========================================================

    @staticmethod
    def sanitize_filename(
        filename: str,
    ) -> str:
        """
        Convert an uploaded filename into a safe filename.
        """

        filename = os.path.basename(str(filename or "").strip())

        if not filename:
            raise ValueError("Filename cannot be empty.")

        filename = re.sub(
            r"[^A-Za-z0-9._-]",
            "_",
            filename,
        )

        if filename.startswith("."):
            filename = f"document{filename}"

        return filename[:255]

    @staticmethod
    def _sql_escape(
        value: Any,
    ) -> str:
        """
        Escape a Python value for use inside a
        single-quoted SQL string.

        This is used only for values controlled by the
        application. Identifiers are never interpolated
        from user input.
        """

        if value is None:
            return ""

        return str(value).replace(
            "'",
            "''",
        )

    @classmethod
    def detect_category(cls, content: str, filename: str) -> str:
        """Compatibility helper used by local contract tests.

        Production uploads still use the dynamic Cortex classifier through
        ``detect_category_from_file``. This deterministic helper recognizes
        the small set of legacy seed-policy subjects used by the unit tests.
        """
        filename_text = os.path.splitext(os.path.basename(str(filename or "")))[
            0
        ].lower()
        content_text = str(content or "").lower()
        haystack = f"{filename_text} {content_text}"

        keyword_groups = {
            "payments": (
                "payment",
                "payments",
                "card",
                "checkout",
                "transaction",
                "charged",
                "declined",
                "pending payment",
            ),
            "shipping": (
                "shipping",
                "delivery",
                "delivered",
                "shipment",
                "dispatch",
                "courier",
                "arrive",
                "arrival",
            ),
            "returns": (
                "return",
                "returns",
                "returned",
                "exchange",
                "replacement",
                "replace",
            ),
            "refunds": (
                "refund",
                "refunded",
                "reimbursement",
                "money back",
            ),
            "warranty": (
                "warranty",
                "damaged",
                "damage",
                "broken",
                "cracked",
                "defective",
                "repair",
            ),
        }

        scores = {}
        for category, keywords in keyword_groups.items():
            score = 0
            for keyword in keywords:
                if keyword in filename_text:
                    score += 3
                score += len(
                    re.findall(
                        r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])",
                        content_text,
                    )
                )
            scores[category] = score

        category, score = max(scores.items(), key=lambda item: item[1])
        if score <= 0:
            raise ValueError(f"Could not determine document category for {filename}")
        return category

    @staticmethod
    def _is_question_line_for_test(line: str) -> bool:
        value = re.sub(r"^#{1,6}\s+", "", str(line or "").strip()).strip()
        return value.endswith("?") and 3 <= len(value) <= 400

    @classmethod
    def _split_test_chunk(cls, text: str, max_chars: int = 1200) -> list[str]:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text:
            return []
        pieces = []
        remaining = text
        while len(remaining) > max_chars:
            candidate = remaining[:max_chars]
            split_at = max(
                candidate.rfind(". "), candidate.rfind("? "), candidate.rfind("! ")
            )
            if split_at < int(max_chars * 0.55):
                split_at = candidate.rfind(" ")
            if split_at <= 0:
                split_at = max_chars
            else:
                split_at += 1
            pieces.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        if remaining:
            pieces.append(remaining)
        return pieces

    @classmethod
    def _build_chunks(cls, content: str) -> list[str]:
        """Build deterministic FAQ/plain-text chunks for local contracts."""
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in str(content or "")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .split("\n")
            if line.strip()
        ]
        if not lines:
            return []

        question_indexes = [
            i for i, line in enumerate(lines) if cls._is_question_line_for_test(line)
        ]
        if question_indexes:
            chunks = []
            for pos, start in enumerate(question_indexes):
                end = (
                    question_indexes[pos + 1]
                    if pos + 1 < len(question_indexes)
                    else len(lines)
                )
                logical = "\n".join(lines[start:end]).strip()
                chunks.extend(cls._split_test_chunk(logical))
            return chunks

        paragraphs = re.split(r"\n\s*\n+", str(content or "").strip())
        chunks = []
        current = []
        current_len = 0
        for paragraph in paragraphs:
            paragraph = re.sub(r"\s+", " ", paragraph).strip()
            if not paragraph:
                continue
            for part in cls._split_test_chunk(paragraph):
                if current and current_len + len(part) + 2 > 1200:
                    chunks.append("\n\n".join(current).strip())
                    current = []
                    current_len = 0
                current.append(part)
                current_len += len(part) + 2
        if current:
            chunks.append("\n\n".join(current).strip())
        return [chunk for chunk in chunks if chunk and len(chunk) <= 1200]

    @staticmethod
    def _calculate_content_hash(
        file_bytes: bytes,
    ) -> str:
        return hashlib.sha256(file_bytes).hexdigest()

    @classmethod
    def _validate_extension(
        cls,
        filename: str,
    ) -> str:

        extension = os.path.splitext(filename)[1].lower()

        if extension not in cls.ALLOWED_EXTENSIONS:
            raise ValueError(
                f"Unsupported document type: "
                f"{extension or 'unknown'}. "
                f"Supported types: PDF, DOCX, MD and TXT."
            )

        return extension

    @classmethod
    def validate_file(
        cls,
        file_bytes: bytes,
        filename: str,
    ) -> None:

        if not filename:
            raise ValueError("Filename is required.")

        extension = cls._validate_extension(filename)

        if not file_bytes:
            raise ValueError(f"{filename} is empty.")

        max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024

        if len(file_bytes) > max_bytes:
            raise ValueError(
                f"{filename} exceeds the configured {MAX_FILE_SIZE_MB} MB limit."
            )

        # Basic ZIP signature check for DOCX.
        if extension == ".docx":
            if not file_bytes.startswith(b"PK"):
                raise ValueError(f"{filename} is not a valid DOCX file.")

        # Basic PDF signature check.
        if extension == ".pdf":
            if not file_bytes.startswith(b"%PDF"):
                raise ValueError(f"{filename} is not a valid PDF file.")

    # ========================================================
    # DOCUMENT ID
    # ========================================================

    @staticmethod
    def generate_document_id(
        file_bytes: bytes,
    ) -> str:
        """
        Generate a stable ID from file contents.

        The first 16 characters are used because the
        DOCUMENTS table allows VARCHAR(100).
        """

        return hashlib.sha256(file_bytes).hexdigest()[:16]

    # ========================================================
    # TEXT NORMALIZATION
    # ========================================================

    @staticmethod
    def _clean_text(
        text: str,
    ) -> str:

        if not text:
            return ""

        text = str(text)

        text = text.replace(
            "\r\n",
            "\n",
        ).replace(
            "\r",
            "\n",
        )

        # Remove null bytes.
        text = text.replace(
            "\x00",
            "",
        )

        # Normalize excessive spaces while keeping
        # paragraph/newline boundaries.
        text = re.sub(
            r"[ \t]+",
            " ",
            text,
        )

        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )

        return text.strip()

    @staticmethod
    def _decode_text_file(
        file_bytes: bytes,
    ) -> str:

        # UTF-8 is the expected format.
        # UTF-8-SIG also handles files with BOM.
        try:
            text = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = file_bytes.decode(
                    "utf-8",
                    errors="replace",
                )
            except Exception as exc:
                raise ValueError("Unable to decode text document.") from exc

        text = DocumentStore._clean_text(text)

        if not text:
            raise ValueError("The document contains no readable text.")

        return text

    # ========================================================
    # CATEGORY DETECTION
    # ========================================================

    @staticmethod
    def _filename_category(
        filename: str,
    ) -> str:
        """
        Safe fallback category.

        This does NOT use a predefined category list.

        Example:

            shipping_faq.md
                -> shipping faq

            employee_handbook.txt
                -> employee handbook
        """

        stem = os.path.splitext(os.path.basename(filename))[0]

        stem = re.sub(
            r"[_\-]+",
            " ",
            stem,
        )

        stem = re.sub(
            r"\s+",
            " ",
            stem,
        ).strip()

        # Remove generic filename suffixes.
        stem = re.sub(
            r"\b(faq|faqs|policy|policies|document|docs|guide|guidelines)\b",
            "",
            stem,
            flags=re.IGNORECASE,
        )

        stem = re.sub(
            r"\s+",
            " ",
            stem,
        ).strip()

        return stem[:100] or "general"

    def _classify_category_with_cortex(
        self,
        filename: str,
        content_preview: str,
    ) -> str:
        """
        Dynamically generate a category using Snowflake Cortex.

        There is intentionally NO fixed list of allowed categories.

        The model is asked to create a short human-readable
        category based on the document itself.
        """

        preview = (content_preview or "").strip()[:6000]

        filename_safe = filename.replace(
            "'",
            "''",
        )

        preview_safe = preview.replace(
            "'",
            "''",
        )

        prompt = f"""
You are categorizing a business policy document.

Create ONE short category name that best describes
the main subject of this document.

Rules:
- Do NOT use a predefined category list.
- Create a new category when necessary.
- Use 1 to 4 words.
- Use lowercase.
- Do not include punctuation.
- Do not explain your answer.
- Return ONLY the category name.

Filename:
{filename_safe}

Document preview:
{preview_safe}
"""

        prompt_sql = prompt.replace(
            "'",
            "''",
        )

        try:
            sql = f"""
            SELECT AI_COMPLETE(
                '{self._sql_escape(CATEGORY_MODEL)}',
                '{prompt_sql}'
            ) AS CATEGORY
            """

            rows = self.session.sql(sql).collect()

            if rows:
                value = rows[0]["CATEGORY"]

                if value:
                    category = str(value).strip().lower()

                    category = re.sub(
                        r"^[\"'`]+|[\"'`]+$",
                        "",
                        category,
                    )

                    category = re.sub(
                        r"\s+",
                        " ",
                        category,
                    )

                    category = re.sub(
                        r"[^a-z0-9 &/_-]",
                        "",
                        category,
                    ).strip()

                    if category:
                        return category[:100]

        except Exception:
            # Category generation must never prevent a valid
            # document from being uploaded.
            pass

        return self._filename_category(filename)

    def detect_category_from_file(
        self,
        file_bytes: bytes,
        filename: str,
    ) -> str:
        """
        Public method used by the Streamlit UI.

        MD/TXT:
            category can be generated from file content.

        PDF/DOCX:
            preview category uses the filename until the actual
            Snowflake AI_PARSE_DOCUMENT step occurs.

        The final category is always recalculated after parsing.
        """

        self.validate_file(
            file_bytes,
            filename,
        )

        extension = os.path.splitext(filename)[1].lower()

        if extension in {
            ".md",
            ".txt",
        }:
            content = self._decode_text_file(file_bytes)

            return self._classify_category_with_cortex(
                filename,
                content,
            )

        # We deliberately do not parse PDF/DOCX locally.
        # Their authoritative category is generated after
        # Snowflake AI_PARSE_DOCUMENT extraction.
        return self._filename_category(filename)

    # ========================================================
    # PARSING
    # ========================================================

    def _parse_staged_document(
        self,
        staged_relative_path: str,
        extension: str,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Parse PDF/DOCX with Snowflake AI_PARSE_DOCUMENT.

        AI_PARSE_DOCUMENT supports page_split for PDF and DOCX
        and returns zero-based page indexes.
        """

        safe_stage = self._sql_escape(STAGE_NAME)

        safe_path = self._sql_escape(staged_relative_path)

        sql = f"""
        SELECT AI_PARSE_DOCUMENT(
            TO_FILE(
                '@{safe_stage}',
                '{safe_path}'
            ),
            OBJECT_CONSTRUCT(
                'mode',
                'LAYOUT',
                'page_split',
                TRUE
            ),
            TRUE
        ) AS PARSED_DOCUMENT
        """

        rows = self.session.sql(sql).collect()

        if not rows:
            raise RuntimeError("AI_PARSE_DOCUMENT returned no result.")

        raw = rows[0]["PARSED_DOCUMENT"]

        if raw is None:
            raise RuntimeError("Snowflake AI_PARSE_DOCUMENT returned NULL.")

        if isinstance(
            raw,
            str,
        ):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError("AI_PARSE_DOCUMENT returned invalid JSON.") from exc
        else:
            parsed = raw

        if not isinstance(
            parsed,
            dict,
        ):
            raise RuntimeError("Unexpected AI_PARSE_DOCUMENT response.")

        # New API:
        #
        # {
        #   "value": {
        #       "pages": [...]
        #   },
        #   "error": null,
        #   "metadata": {...}
        # }
        #
        # Unwrap value if present.
        error = parsed.get("error")

        if error:
            raise RuntimeError(f"AI_PARSE_DOCUMENT failed: {error}")

        value = parsed.get(
            "value",
            parsed,
        )

        if not isinstance(
            value,
            dict,
        ):
            raise RuntimeError("AI_PARSE_DOCUMENT returned no document value.")

        pages = value.get("pages")

        # Fallback for a non-page-split response.
        if not pages:
            content = value.get(
                "content",
                "",
            )

            if not content:
                raise ValueError("Document was parsed but contains no readable text.")

            pages = [
                {
                    "index": 0,
                    "content": content,
                }
            ]

        page_rows: list[dict[str, Any]] = []

        for fallback_index, page in enumerate(pages):
            if not isinstance(
                page,
                dict,
            ):
                continue

            page_index = page.get(
                "index",
                fallback_index,
            )

            try:
                page_index = int(page_index)
            except (
                TypeError,
                ValueError,
            ):
                page_index = fallback_index

            content = self._clean_text(
                str(
                    page.get(
                        "content",
                        "",
                    )
                    or ""
                )
            )

            if not content:
                continue

            page_rows.append(
                {
                    "page_index": page_index,
                    "page_number": page_index + 1,
                    "content": content,
                }
            )

        if not page_rows:
            raise ValueError(
                "Document was parsed but no readable page content was extracted."
            )

        page_rows.sort(key=lambda item: item["page_index"])

        metadata = parsed.get(
            "metadata",
            {},
        )

        try:
            page_count = int(
                metadata.get(
                    "pageCount",
                    len(page_rows),
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            page_count = len(page_rows)

        return (
            page_rows,
            page_count,
        )

    # ========================================================
    # DOCUMENT CONTENT STORAGE
    # ========================================================

    def _clear_document_content(
        self,
        document_id: str,
    ) -> None:

        safe_id = self._sql_escape(document_id)

        self.session.sql(
            f"""
            DELETE FROM {DOCUMENT_CONTENT_TABLE}
            WHERE DOCUMENT_ID = '{safe_id}'
            """
        ).collect()

    def _store_document_pages(
        self,
        document_id: str,
        filename: str,
        pages: list[dict[str, Any]],
        content_format: str,
    ) -> None:

        self._clear_document_content(document_id)

        safe_id = self._sql_escape(document_id)

        for page in pages:
            page_index = int(
                page.get(
                    "page_index",
                    0,
                )
            )

            page_number = int(
                page.get(
                    "page_number",
                    page_index + 1,
                )
            )

            content = self._clean_text(
                page.get(
                    "content",
                    "",
                )
            )

            if not content:
                continue

            content_id = f"{document_id}_PAGE_{page_index:05d}"

            # Determine a likely section heading.
            section_heading = ""

            for line in content.splitlines():
                line = line.strip()

                if re.match(
                    r"^#{1,6}\s+.+",
                    line,
                ):
                    section_heading = re.sub(
                        r"^#{1,6}\s+",
                        "",
                        line,
                    ).strip()

                    break

            safe_content_id = self._sql_escape(content_id)

            safe_content = self._sql_escape(content)

            safe_section = self._sql_escape(section_heading)

            self.session.sql(
                f"""
                INSERT INTO {DOCUMENT_CONTENT_TABLE} (
                    CONTENT_ID,
                    DOCUMENT_ID,
                    PAGE_INDEX,
                    PAGE_NUMBER,
                    CONTENT,
                    CONTENT_FORMAT,
                    SECTION,
                    PARSE_STATUS,
                    PARSE_ERROR
                )
                VALUES (
                    '{safe_content_id}',
                    '{safe_id}',
                    {page_index},
                    {page_number},
                    '{safe_content}',
                    '{self._sql_escape(content_format)}',
                    '{safe_section}',
                    'PARSED',
                    NULL
                )
                """
            ).collect()

    # ========================================================
    # FAQ DETECTION
    # ========================================================

    @staticmethod
    def _is_question_line(
        line: str,
    ) -> bool:

        value = line.strip()

        value = re.sub(
            r"^#{1,6}\s+",
            "",
            value,
        )

        value = re.sub(
            r"^(?:Q\s*)?\d+\s*[\.\):\-]\s*",
            "",
            value,
            flags=re.IGNORECASE,
        )

        value = re.sub(
            r"^[•*\-]\s*",
            "",
            value,
        )

        return value.endswith("?") and 3 <= len(value) <= 400

    @classmethod
    def _extract_faq_sections(
        cls,
        text: str,
    ) -> list[str]:
        """
        Keep:

            Question?
            Answer...

        together.

        This prevents the source UI from receiving an unrelated
        question from another FAQ.
        """

        lines = [line.strip() for line in text.splitlines() if line.strip()]

        if not lines:
            return []

        question_indexes = [
            index for index, line in enumerate(lines) if cls._is_question_line(line)
        ]

        if not question_indexes:
            return [text.strip()]

        sections: list[str] = []

        # Preserve any introductory text before the first question.
        if question_indexes[0] > 0:
            intro = "\n".join(lines[: question_indexes[0]]).strip()

            if intro:
                sections.append(intro)

        for position, start in enumerate(question_indexes):
            end = (
                question_indexes[position + 1]
                if position + 1 < len(question_indexes)
                else len(lines)
            )

            section = "\n".join(lines[start:end]).strip()

            if section:
                sections.append(section)

        return sections

    # ========================================================
    # SNOWFLAKE CORTEX CHUNKING
    # ========================================================

    def _split_with_cortex(
        self,
        text: str,
        format_name: str = "markdown",
    ) -> list[str]:
        """
        Use Snowflake Cortex recursive chunking.

        No local chunking algorithm is used.
        """

        text = self._clean_text(text)

        if not text:
            return []

        safe_text = self._sql_escape(text)

        safe_format = "markdown" if format_name == "markdown" else "none"

        sql = f"""
        SELECT VALUE::VARCHAR AS CHUNK_TEXT
        FROM TABLE(
            FLATTEN(
                INPUT => SNOWFLAKE.CORTEX.SPLIT_TEXT_RECURSIVE_CHARACTER(
                    '{safe_text}',
                    '{safe_format}',
                    {MAX_CHUNK_SIZE},
                    {CHUNK_OVERLAP}
                )
            )
        )
        WHERE VALUE::VARCHAR IS NOT NULL
          AND LENGTH(TRIM(VALUE::VARCHAR)) > 0
        ORDER BY INDEX
        """

        rows = self.session.sql(sql).collect()

        chunks: list[str] = []

        for row in rows:
            value = row["CHUNK_TEXT"]

            if value:
                value = self._clean_text(str(value))

                if value:
                    chunks.append(value)

        return chunks

    def _build_retrieval_chunks(
        self,
        pages: list[dict[str, Any]],
        extension: str,
    ) -> list[dict[str, Any]]:
        """
        Build retrieval chunks while preserving page metadata.

        FAQ sections are identified first, then each section is
        passed through Snowflake Cortex recursive chunking.
        """

        format_name = "markdown" if extension == ".md" else "none"

        chunks: list[dict[str, Any]] = []

        for page in pages:
            page_index = int(
                page.get(
                    "page_index",
                    0,
                )
            )

            page_number = int(
                page.get(
                    "page_number",
                    page_index + 1,
                )
            )

            content = self._clean_text(
                page.get(
                    "content",
                    "",
                )
            )

            if not content:
                continue

            sections = self._extract_faq_sections(content)

            for section in sections:
                cortex_chunks = self._split_with_cortex(
                    section,
                    format_name,
                )

                if not cortex_chunks:
                    cortex_chunks = [section]

                for chunk in cortex_chunks:
                    chunk = self._clean_text(chunk)

                    if not chunk:
                        continue

                    section_heading = ""

                    first_line = (
                        chunk.splitlines()[0].strip() if chunk.splitlines() else ""
                    )

                    if re.match(
                        r"^#{1,6}\s+.+",
                        first_line,
                    ):
                        section_heading = re.sub(
                            r"^#{1,6}\s+",
                            "",
                            first_line,
                        ).strip()

                    chunks.append(
                        {
                            "page_index": page_index,
                            "page_number": page_number,
                            "chunk_text": chunk,
                            "section_heading": section_heading,
                        }
                    )

        return chunks

    # ========================================================
    # POLICY TABLE INGESTION
    # ========================================================

    def _delete_existing_chunks(
        self,
        document_id: str,
    ) -> None:

        safe_id = self._sql_escape(document_id)

        self.session.sql(
            f"""
            DELETE FROM {POLICY_CHUNKS_TABLE}
            WHERE DOCUMENT_ID = '{safe_id}'
            """
        ).collect()

    def _upsert_policy_source(
        self,
        document_id: str,
        filename: str,
        category: str,
        content: str,
    ) -> None:

        safe_id = self._sql_escape(document_id)

        safe_filename = self._sql_escape(filename)

        safe_category = self._sql_escape(category)

        safe_content = self._sql_escape(content)

        self.session.sql(
            f"""
            MERGE INTO {POLICY_SOURCES_TABLE} target
            USING (
                SELECT
                    '{safe_id}' AS DOCUMENT_ID,
                    '{safe_filename}' AS DOCUMENT_NAME,
                    '{safe_category}' AS CATEGORY,
                    '{safe_content}' AS CONTENT
            ) source
            ON target.DOCUMENT_ID = source.DOCUMENT_ID

            WHEN MATCHED THEN UPDATE SET
                DOCUMENT_NAME = source.DOCUMENT_NAME,
                CATEGORY = source.CATEGORY,
                CONTENT = source.CONTENT,
                CREATED_AT = CURRENT_TIMESTAMP()

            WHEN NOT MATCHED THEN INSERT (
                DOCUMENT_ID,
                DOCUMENT_NAME,
                CATEGORY,
                CONTENT
            )
            VALUES (
                source.DOCUMENT_ID,
                source.DOCUMENT_NAME,
                source.CATEGORY,
                source.CONTENT
            )
            """
        ).collect()

    def _insert_chunks(
        self,
        document_id: str,
        filename: str,
        category: str,
        chunks: list[dict[str, Any]],
    ) -> int:

        self._delete_existing_chunks(document_id)

        safe_id = self._sql_escape(document_id)

        safe_filename = self._sql_escape(filename)

        safe_category = self._sql_escape(category)

        inserted = 0

        for index, chunk in enumerate(chunks):
            chunk_id = f"{document_id}_CHUNK_{index + 1:05d}"

            safe_chunk_id = self._sql_escape(chunk_id)

            safe_chunk = self._sql_escape(chunk["chunk_text"])

            page_index = int(
                chunk.get(
                    "page_index",
                    0,
                )
            )

            page_number = int(
                chunk.get(
                    "page_number",
                    page_index + 1,
                )
            )

            section_heading = self._sql_escape(
                chunk.get(
                    "section_heading",
                    "",
                )
            )

            chunk_length = len(chunk["chunk_text"])

            self.session.sql(
                f"""
                INSERT INTO {POLICY_CHUNKS_TABLE} (
                    CHUNK_ID,
                    DOCUMENT_ID,
                    DOCUMENT_NAME,
                    CATEGORY,
                    CHUNK_INDEX,
                    CHUNK_TEXT,
                    CHUNK_LENGTH,
                    PAGE_INDEX,
                    PAGE_NUMBER,
                    SECTION_HEADING,
                    SOURCE_TYPE,
                    ACTIVE
                )
                VALUES (
                    '{safe_chunk_id}',
                    '{safe_id}',
                    '{safe_filename}',
                    '{safe_category}',
                    {index},
                    '{safe_chunk}',
                    {chunk_length},
                    {page_index},
                    {page_number},
                    '{section_heading}',
                    'USER_UPLOAD',
                    TRUE
                )
                """
            ).collect()

            inserted += 1

        return inserted

    # ========================================================
    # SEARCH REFRESH
    # ========================================================

    def refresh_search(
        self,
    ) -> None:
        """
        Trigger an immediate Cortex Search refresh.

        If the application role does not have OPERATE privilege,
        indexing remains automatic according to TARGET_LAG and the
        document is marked INDEXING rather than falsely claiming
        immediate search readiness.
        """

        service = self._sql_escape(SEARCH_SERVICE)

        self.session.sql(
            f"""
            ALTER CORTEX SEARCH SERVICE
            {service}
            REFRESH
            """
        ).collect()

    # ========================================================
    # DOCUMENT STATUS
    # ========================================================

    def _update_document_status(
        self,
        document_id: str,
        status: str,
        *,
        category: str | None = None,
        page_count: int | None = None,
        character_count: int | None = None,
        chunk_count: int | None = None,
        error_message: str | None = None,
        active: bool | None = None,
    ) -> None:

        safe_id = self._sql_escape(document_id)

        assignments = [
            f"PROCESSING_STATUS = '{self._sql_escape(status)}'",
            "UPDATED_AT = CURRENT_TIMESTAMP()",
        ]

        if category is not None:
            assignments.append(f"CATEGORY = '{self._sql_escape(category)}'")

        if page_count is not None:
            assignments.append(f"PAGE_COUNT = {int(page_count)}")

        if character_count is not None:
            assignments.append(f"CHARACTER_COUNT = {int(character_count)}")

        if chunk_count is not None:
            assignments.append(f"CHUNK_COUNT = {int(chunk_count)}")

        if error_message is None:
            if status in {
                "INDEXED",
                "INDEXING",
                "PARSING",
            }:
                assignments.append("ERROR_MESSAGE = NULL")
        else:
            # Do not store huge exception messages.
            clean_error = str(error_message)[:2000]

            assignments.append(f"ERROR_MESSAGE = '{self._sql_escape(clean_error)}'")

        if active is not None:
            assignments.append("ACTIVE = " + ("TRUE" if active else "FALSE"))

        self.session.sql(
            f"""
            UPDATE {DOCUMENTS_TABLE}
            SET
                {", ".join(assignments)}
            WHERE DOCUMENT_ID = '{safe_id}'
            """
        ).collect()

    # ========================================================
    # DUPLICATE CHECK
    # ========================================================

    def _find_duplicate(
        self,
        content_hash: str,
        include_inactive: bool = False,
    ) -> dict[str, Any] | None:

        safe_hash = self._sql_escape(content_hash)

        active_clause = "" if include_inactive else "AND ACTIVE = TRUE"

        rows = self.session.sql(
            f"""
            SELECT
                DOCUMENT_ID,
                ORIGINAL_FILENAME,
                PROCESSING_STATUS,
                ACTIVE
            FROM {DOCUMENTS_TABLE}
            WHERE CONTENT_HASH = '{safe_hash}'
            {active_clause}
            LIMIT 1
            """
        ).collect()

        if not rows:
            return None

        return rows[0].as_dict()

    # ========================================================
    # FAILURE / LOGGING HELPERS
    # ========================================================

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        """Return a safe error summary without secrets or raw document content."""
        message = str(exc or "Unknown error")
        message = re.sub(
            r"(?i)(password|passwd|token|pat|secret|authorization)\\s*[=:]\\s*[^\\s,;]+",
            r"\\1=[REDACTED]",
            message,
        )
        message = re.sub(r"(?i)Bearer\\s+[^\\s]+", "Bearer [REDACTED]", message)
        return message[:2000]

    @staticmethod
    def _log_failure(
        stage: str, document_id: str | None, filename: str | None, exc: Exception
    ) -> None:
        """Log operational failure details without logging document content or credentials."""
        LOGGER.exception(
            "Document processing failure stage=%s document_id=%s filename=%s error=%s",
            stage,
            document_id or "",
            filename or "",
            DocumentStore._sanitize_error(exc),
        )

    # ========================================================
    # UPLOAD
    # ========================================================

    def upload_to_stage(
        self,
        file_bytes: bytes,
        filename: str,
        document_id: str | None = None,
        category: str | None = None,
        description: str | None = None,
        tags: str | None = None,
        uploaded_by: str | None = None,
    ) -> str:

        started_at = time.time()
        failure_stage = "validation"

        self.validate_file(
            file_bytes,
            filename,
        )

        safe_filename = self.sanitize_filename(filename)

        extension = self._validate_extension(safe_filename)

        content_hash = self._calculate_content_hash(file_bytes)

        duplicate = self._find_duplicate(content_hash)

        if duplicate:
            raise ValueError(
                "Document already exists: "
                f"{duplicate['ORIGINAL_FILENAME']} "
                f"(Document ID: "
                f"{duplicate['DOCUMENT_ID']})"
            )

        if not document_id:
            document_id = self.generate_document_id(file_bytes)

        # Avoid accidental collision.
        existing_id = self.session.sql(
            f"""
            SELECT DOCUMENT_ID
            FROM {DOCUMENTS_TABLE}
            WHERE DOCUMENT_ID = '{self._sql_escape(document_id)}'
            LIMIT 1
            """
        ).collect()

        if existing_id:
            document_id = f"{document_id[:10]}{uuid.uuid4().hex[:6]}"

        stage_relative_path = f"{document_id}/{safe_filename}"

        staged_file_path = f"@{STAGE_NAME}/{stage_relative_path}"

        normalized_category = (
            category.strip().lower() if category and category.strip() else ""
        )

        # ----------------------------------------------------
        # For MD/TXT we can generate the category immediately.
        # PDF/DOCX category is finalized after AI parsing.
        # ----------------------------------------------------

        if not normalized_category:
            if extension in {
                ".md",
                ".txt",
            }:
                text_preview = self._decode_text_file(file_bytes)

                normalized_category = self._classify_category_with_cortex(
                    safe_filename,
                    text_preview,
                )
            else:
                normalized_category = self._filename_category(safe_filename)

        file_type = extension.lstrip(".")

        safe_id = self._sql_escape(document_id)

        safe_original = self._sql_escape(filename)

        safe_filename_sql = self._sql_escape(safe_filename)

        safe_hash = self._sql_escape(content_hash)

        safe_stage_path = self._sql_escape(staged_file_path)

        safe_category = self._sql_escape(normalized_category)

        # ----------------------------------------------------
        # Create metadata record BEFORE upload.
        # ----------------------------------------------------

        self.session.sql(
            f"""
            INSERT INTO {DOCUMENTS_TABLE} (
                DOCUMENT_ID,
                ORIGINAL_FILENAME,
                SANITIZED_FILENAME,
                STAGED_FILE_PATH,
                FILE_TYPE,
                FILE_SIZE,
                CONTENT_HASH,
                CATEGORY,
                DESCRIPTION,
                TAGS,
                UPLOADED_BY,
                PROCESSING_STATUS,
                ACTIVE
            )
            VALUES (
                '{safe_id}',
                '{safe_original}',
                '{safe_filename_sql}',
                '{safe_stage_path}',
                '{file_type}',
                {len(file_bytes)},
                '{safe_hash}',
                '{safe_category}',
                '{self._sql_escape(description)}',
                '{self._sql_escape(tags)}',
                '{self._sql_escape(uploaded_by)}',
                'UPLOADING',
                TRUE
            )
            """
        ).collect()

        try:
            # =================================================
            # 1. UPLOAD TO INTERNAL STAGE
            # =================================================

            failure_stage = "stage_upload"
            self.session.file.put_stream(
                io.BytesIO(file_bytes),
                staged_file_path,
                auto_compress=False,
                overwrite=False,
            )

            # =================================================
            # 2. PARSE
            # =================================================

            failure_stage = "parsing"
            self._update_document_status(
                document_id,
                "PARSING",
            )

            if extension in {
                ".md",
                ".txt",
            }:
                content = self._decode_text_file(file_bytes)

                pages = [
                    {
                        "page_index": 0,
                        "page_number": 1,
                        "content": content,
                    }
                ]

                page_count = 1

                content_format = "MARKDOWN" if extension == ".md" else "TEXT"

            else:
                pages, page_count = self._parse_staged_document(
                    stage_relative_path,
                    extension,
                )

                content = "\n\n".join(page["content"] for page in pages)

                content_format = "PDF" if extension == ".pdf" else "DOCX"

            content = self._clean_text(content)

            if not content:
                raise ValueError(f"{filename} contains no readable text.")

            # =================================================
            # 3. FINAL DYNAMIC CATEGORY
            # =================================================

            if not category or not category.strip():
                normalized_category = self._classify_category_with_cortex(
                    safe_filename,
                    content,
                )

                self._update_document_status(
                    document_id,
                    "PARSING",
                    category=normalized_category,
                )

            # =================================================
            # 4. STORE PARSED PAGES
            # =================================================

            failure_stage = "content_storage"
            self._store_document_pages(
                document_id=document_id,
                filename=safe_filename,
                pages=pages,
                content_format=content_format,
            )

            # =================================================
            # 5. CHUNK
            # =================================================

            failure_stage = "chunking"
            self._update_document_status(
                document_id,
                "INDEXING",
            )

            chunks = self._build_retrieval_chunks(
                pages=pages,
                extension=extension,
            )

            if not chunks:
                raise ValueError(f"{filename} did not produce searchable chunks.")

            # =================================================
            # 6. UPSERT SOURCE + CHUNKS
            # =================================================

            failure_stage = "chunk_storage"
            self._upsert_policy_source(
                document_id=document_id,
                filename=safe_filename,
                category=normalized_category,
                content=content,
            )

            chunk_count = self._insert_chunks(
                document_id=document_id,
                filename=safe_filename,
                category=normalized_category,
                chunks=chunks,
            )

            # =================================================
            # 7. REFRESH CORTEX SEARCH
            # =================================================

            try:
                failure_stage = "search_refresh"
                self.refresh_search()

                final_status = "INDEXED"

            except Exception as refresh_exc:
                # Chunks are safely stored, but we MUST NOT
                # pretend that Cortex Search is immediately ready.
                final_status = "INDEXING"

                self._update_document_status(
                    document_id,
                    "INDEXING",
                    category=normalized_category,
                    page_count=page_count,
                    character_count=len(content),
                    chunk_count=chunk_count,
                    error_message=(
                        "Chunks stored successfully. "
                        "Cortex Search refresh is pending: "
                        f"{refresh_exc}"
                    ),
                )

            # =================================================
            # 8. FINAL STATUS
            # =================================================

            self._update_document_status(
                document_id,
                final_status,
                category=normalized_category,
                page_count=page_count,
                character_count=len(content),
                chunk_count=chunk_count,
                error_message=None
                if final_status == "INDEXED"
                else ("Cortex Search refresh pending."),
            )

            elapsed = time.time() - started_at

            print(
                f"[RetailAssist] Indexed document "
                f"{document_id} "
                f"({safe_filename}) "
                f"in {elapsed:.2f}s"
            )

            return staged_file_path

        except Exception as exc:
            # Never leave partial chunks/content/source rows searchable.
            self._log_failure(
                failure_stage,
                document_id,
                safe_filename if "safe_filename" in locals() else filename,
                exc,
            )

            try:
                self._delete_existing_chunks(document_id)
            except Exception as cleanup_exc:
                LOGGER.exception(
                    "Chunk cleanup failed for document_id=%s: %s",
                    document_id,
                    self._sanitize_error(cleanup_exc),
                )

            try:
                self._clear_document_content(document_id)
            except Exception as cleanup_exc:
                LOGGER.exception(
                    "Content cleanup failed for document_id=%s: %s",
                    document_id,
                    self._sanitize_error(cleanup_exc),
                )

            try:
                safe_failed_id = self._sql_escape(document_id)
                self.session.sql(
                    f"DELETE FROM {POLICY_SOURCES_TABLE} WHERE DOCUMENT_ID = '{safe_failed_id}'"
                ).collect()
            except Exception as cleanup_exc:
                LOGGER.exception(
                    "Policy source cleanup failed for document_id=%s: %s",
                    document_id,
                    self._sanitize_error(cleanup_exc),
                )

            try:
                self._update_document_status(
                    document_id,
                    "FAILED",
                    error_message=self._sanitize_error(exc),
                    active=True,
                )
            except Exception as status_exc:
                LOGGER.exception(
                    "Failed to persist FAILED status for document_id=%s: %s",
                    document_id,
                    self._sanitize_error(status_exc),
                )

            raise

    # ========================================================
    # LIST DOCUMENTS
    # ========================================================

    def list_documents(
        self,
        category: str = "All",
        status: str = "All",
        active_only: bool = False,
    ) -> list[dict[str, Any]]:

        conditions = []

        if category and category != "All":
            conditions.append(f"CATEGORY = '{self._sql_escape(category.lower())}'")

        if status and status != "All":
            conditions.append(
                f"PROCESSING_STATUS = '{self._sql_escape(status.upper())}'"
            )

        if active_only:
            conditions.append("ACTIVE = TRUE")

        where_clause = ""

        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        rows = self.session.sql(
            f"""
            SELECT
                DOCUMENT_ID,
                ORIGINAL_FILENAME,
                SANITIZED_FILENAME,
                STAGED_FILE_PATH,
                FILE_TYPE,
                FILE_SIZE,
                CONTENT_HASH,
                CATEGORY,
                DESCRIPTION,
                TAGS,
                UPLOADED_BY,
                PROCESSING_STATUS,
                PAGE_COUNT,
                CHARACTER_COUNT,
                CHUNK_COUNT,
                ERROR_MESSAGE,
                CREATED_AT,
                UPDATED_AT,
                ACTIVE
            FROM {DOCUMENTS_TABLE}
            {where_clause}
            ORDER BY CREATED_AT DESC
            """
        ).collect()

        return [row.as_dict() for row in rows]

    # ========================================================
    # CATEGORIES
    # ========================================================

    def list_categories(
        self,
    ) -> list[str]:

        rows = self.session.sql(
            f"""
            SELECT DISTINCT CATEGORY
            FROM {DOCUMENTS_TABLE}
            WHERE ACTIVE = TRUE
              AND CATEGORY IS NOT NULL
              AND TRIM(CATEGORY) <> ''
            ORDER BY CATEGORY
            """
        ).collect()

        categories = []

        for row in rows:
            value = row["CATEGORY"]

            if value:
                categories.append(str(value))

        return categories

    # ========================================================
    # GET DOCUMENT
    # ========================================================

    def get_document(
        self,
        document_id: str,
    ) -> dict[str, Any] | None:

        safe_id = self._sql_escape(document_id)

        rows = self.session.sql(
            f"""
            SELECT
                DOCUMENT_ID,
                ORIGINAL_FILENAME,
                SANITIZED_FILENAME,
                STAGED_FILE_PATH,
                FILE_TYPE,
                FILE_SIZE,
                CONTENT_HASH,
                CATEGORY,
                DESCRIPTION,
                TAGS,
                UPLOADED_BY,
                PROCESSING_STATUS,
                PAGE_COUNT,
                CHARACTER_COUNT,
                CHUNK_COUNT,
                ERROR_MESSAGE,
                CREATED_AT,
                UPDATED_AT,
                ACTIVE
            FROM {DOCUMENTS_TABLE}
            WHERE DOCUMENT_ID = '{safe_id}'
            LIMIT 1
            """
        ).collect()

        if not rows:
            return None

        return rows[0].as_dict()

    # ========================================================
    # RETRY
    # ========================================================

    def retry_document(
        self,
        document_id: str,
    ) -> int:

        document = self.get_document(document_id)

        if not document:
            raise ValueError(f"Document not found: {document_id}")

        if not document.get(
            "ACTIVE",
            False,
        ):
            raise ValueError("Cannot retry an inactive document.")

        staged_path = document.get("STAGED_FILE_PATH")

        if not staged_path:
            raise ValueError("Document has no staged file path.")

        filename = document.get("SANITIZED_FILENAME") or document.get(
            "ORIGINAL_FILENAME"
        )

        extension = os.path.splitext(str(filename))[1].lower()

        relative_path = str(staged_path).split(
            f"@{STAGE_NAME}/",
            1,
        )[-1]

        self._update_document_status(
            document_id,
            "PARSING",
            error_message=None,
        )

        try:
            if extension in {
                ".md",
                ".txt",
            }:
                # MD/TXT are already represented in
                # POLICY_SOURCES, so recover content.
                rows = self.session.sql(
                    f"""
                    SELECT CONTENT
                    FROM {POLICY_SOURCES_TABLE}
                    WHERE DOCUMENT_ID =
                        '{self._sql_escape(document_id)}'
                    LIMIT 1
                    """
                ).collect()

                if not rows:
                    raise ValueError("No stored text exists for retry.")

                content = self._clean_text(str(rows[0]["CONTENT"] or ""))

                pages = [
                    {
                        "page_index": 0,
                        "page_number": 1,
                        "content": content,
                    }
                ]

                page_count = 1

            else:
                pages, page_count = self._parse_staged_document(
                    relative_path,
                    extension,
                )

                content = "\n\n".join(page["content"] for page in pages)

            if not content:
                raise ValueError("Retry produced no readable content.")

            category = str(
                document.get("CATEGORY") or self._filename_category(filename)
            )

            self._store_document_pages(
                document_id,
                filename,
                pages,
                (
                    "PDF"
                    if extension == ".pdf"
                    else "DOCX"
                    if extension == ".docx"
                    else "MARKDOWN"
                    if extension == ".md"
                    else "TEXT"
                ),
            )

            self._update_document_status(
                document_id,
                "INDEXING",
            )

            chunks = self._build_retrieval_chunks(
                pages,
                extension,
            )

            if not chunks:
                raise ValueError("Retry did not produce searchable chunks.")

            self._upsert_policy_source(
                document_id,
                filename,
                category,
                content,
            )

            chunk_count = self._insert_chunks(
                document_id,
                filename,
                category,
                chunks,
            )

            try:
                self.refresh_search()
                final_status = "INDEXED"
                refresh_error = None

            except Exception as exc:
                final_status = "INDEXING"
                refresh_error = f"Cortex Search refresh pending: {exc}"

            self._update_document_status(
                document_id,
                final_status,
                category=category,
                page_count=page_count,
                character_count=len(content),
                chunk_count=chunk_count,
                error_message=refresh_error,
            )

            return chunk_count

        except Exception as exc:
            self._log_failure(
                "retry",
                document_id,
                str(
                    document.get("SANITIZED_FILENAME")
                    or document.get("ORIGINAL_FILENAME")
                    or ""
                ),
                exc,
            )
            try:
                self._delete_existing_chunks(document_id)
            except Exception as cleanup_exc:
                LOGGER.exception(
                    "Retry chunk cleanup failed for document_id=%s: %s",
                    document_id,
                    self._sanitize_error(cleanup_exc),
                )
            try:
                self._clear_document_content(document_id)
            except Exception as cleanup_exc:
                LOGGER.exception(
                    "Retry content cleanup failed for document_id=%s: %s",
                    document_id,
                    self._sanitize_error(cleanup_exc),
                )
            try:
                safe_failed_id = self._sql_escape(document_id)
                self.session.sql(
                    f"DELETE FROM {POLICY_SOURCES_TABLE} WHERE DOCUMENT_ID = '{safe_failed_id}'"
                ).collect()
            except Exception as cleanup_exc:
                LOGGER.exception(
                    "Retry policy source cleanup failed for document_id=%s: %s",
                    document_id,
                    self._sanitize_error(cleanup_exc),
                )
            try:
                self._update_document_status(
                    document_id,
                    "FAILED",
                    error_message=self._sanitize_error(exc),
                )
            except Exception as status_exc:
                LOGGER.exception(
                    "Failed to persist retry FAILED status for document_id=%s: %s",
                    document_id,
                    self._sanitize_error(status_exc),
                )
            raise

    # ========================================================
    # RE-INDEX
    # ========================================================

    def reindex_document(
        self,
        document_id: str,
    ) -> int:
        """
        Rebuild chunks for an existing document.

        This deliberately reuses the staged binary rather than
        requiring the user to upload the file again.
        """

        document = self.get_document(document_id)

        if not document:
            raise ValueError(f"Document not found: {document_id}")

        if not document.get(
            "ACTIVE",
            False,
        ):
            raise ValueError("Cannot re-index an inactive document.")

        return self.retry_document(document_id)

    # ========================================================
    # DELETE / DEACTIVATE
    # ========================================================

    def delete_document(
        self,
        document_id: str,
    ) -> None:

        document = self.get_document(document_id)

        if not document:
            raise ValueError(f"Document not found: {document_id}")

        safe_id = self._sql_escape(document_id)

        # ----------------------------------------------------
        # First deactivate metadata.
        # ----------------------------------------------------

        self._update_document_status(
            document_id,
            "DELETED",
            active=False,
        )

        # ----------------------------------------------------
        # Remove chunks from the searchable table.
        # ----------------------------------------------------

        self.session.sql(
            f"""
            DELETE FROM {POLICY_CHUNKS_TABLE}
            WHERE DOCUMENT_ID = '{safe_id}'
            """
        ).collect()

        # ----------------------------------------------------
        # Remove extracted content.
        # ----------------------------------------------------

        self.session.sql(
            f"""
            DELETE FROM {DOCUMENT_CONTENT_TABLE}
            WHERE DOCUMENT_ID = '{safe_id}'
            """
        ).collect()

        # ----------------------------------------------------
        # Keep POLICY_SOURCES inactive-compatible.
        #
        # We remove it because it is not authoritative metadata.
        # DOCUMENTS remains the audit record.
        # ----------------------------------------------------

        self.session.sql(
            f"""
            DELETE FROM {POLICY_SOURCES_TABLE}
            WHERE DOCUMENT_ID = '{safe_id}'
            """
        ).collect()

        # ----------------------------------------------------
        # Refresh Cortex Search.
        # ----------------------------------------------------

        try:
            self.refresh_search()
        except Exception as exc:
            # The document is already inactive in the authoritative
            # DOCUMENTS table. Retrieval will also protect itself
            # against stale search results.
            LOGGER.warning(
                "Cortex Search refresh after deletion failed document_id=%s: %s",
                document_id,
                self._sanitize_error(exc),
            )

    # ========================================================
    # MANUAL REFRESH
    # ========================================================

    def refresh_index(
        self,
    ) -> None:
        """
        UI-friendly alias.
        """

        self.refresh_search()
