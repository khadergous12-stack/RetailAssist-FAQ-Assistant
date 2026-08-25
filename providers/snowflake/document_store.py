from __future__ import annotations

import hashlib
import io
import os
import re

from snowflake.snowpark import Session


STAGE_NAME = os.getenv(
    "SNOWFLAKE_DOCUMENT_STAGE",
    "DOCUMENT_UPLOAD_STAGE",
)

POLICY_SOURCES_TABLE = "RETAIL_ASSIST_DB.RETAIL_ASSIST.POLICY_SOURCES"
POLICY_CHUNKS_TABLE = "RETAIL_ASSIST_DB.RETAIL_ASSIST.POLICY_CHUNKS"
DOCUMENTS_TABLE = "RETAIL_ASSIST_DB.RETAIL_ASSIST.DOCUMENTS"


class DocumentStore:
    """
    Stores uploaded documents and creates retrieval-friendly chunks.

    Important:
    - TXT/MD/PDF/DOCX are all converted to text first.
    - FAQ documents are split as: QUESTION + its ANSWER.
    - This prevents one large PDF/DOCX chunk from containing many unrelated FAQs.
    - No category list is hardcoded here.
    """

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        filename = os.path.basename(filename)
        return re.sub(r"[^A-Za-z0-9._-]", "_", filename)

    @staticmethod
    def _extract_text(file_bytes: bytes, filename: str) -> str:
        extension = os.path.splitext(filename)[1].lower()

        if extension in {".txt", ".md"}:
            return file_bytes.decode("utf-8", errors="replace").strip()

        if extension == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise RuntimeError("PDF uploads require the 'pypdf' package.") from exc

            reader = PdfReader(io.BytesIO(file_bytes))
            return "\n\n".join(
                page.extract_text() or "" for page in reader.pages
            ).strip()

        if extension == ".docx":
            try:
                from docx import Document
            except ImportError as exc:
                raise RuntimeError(
                    "DOCX uploads require the 'python-docx' package."
                ) from exc

            document = Document(io.BytesIO(file_bytes))
            return "\n\n".join(
                paragraph.text.strip()
                for paragraph in document.paragraphs
                if paragraph.text.strip()
            ).strip()

        raise ValueError(f"Unsupported document type: {extension or 'unknown'}")

    @staticmethod
    def _category_scores(text: str, filename: str) -> dict[str, int]:
        """Score the five RetailAssist policy categories from file name and content."""
        haystack = f"{filename}\n{text}".lower()

        patterns = {
            "payments": {
                "payment": 8,
                "payments": 8,
                "card": 6,
                "credit card": 7,
                "debit card": 7,
                "bank": 3,
                "transaction": 5,
                "charged": 5,
                "charge": 4,
                "wallet": 5,
                "checkout": 3,
                "declined": 5,
                "pending payment": 7,
                "cryptocurrency": 5,
            },
            "shipping": {
                "shipping": 8,
                "delivery": 8,
                "shipment": 7,
                "tracking": 7,
                "courier": 5,
                "dispatch": 5,
                "delivered": 5,
                "delivery window": 7,
                "international delivery": 8,
                "business days": 2,
            },
            "refunds": {
                "refund": 10,
                "refunded": 9,
                "refunds": 10,
                "money back": 8,
                "refund reference": 8,
                "refund status": 8,
                "reimbursement": 7,
                "refund has not": 9,
            },
            "returns": {
                "return": 8,
                "returns": 8,
                "returned": 7,
                "return window": 9,
                "exchange": 7,
                "replacement": 3,
                "eligible for return": 9,
                "return policy": 9,
            },
            "warranty": {
                "warranty": 10,
                "warranties": 8,
                "guarantee": 7,
                "accidental damage": 9,
                "defect": 6,
                "defective": 7,
                "repair": 5,
                "covered under warranty": 10,
                "warranty coverage": 10,
            },
        }

        scores = {category: 0 for category in patterns}
        filename_only = os.path.basename(filename).lower()

        # Filename is the strongest signal because FAQ files commonly use names
        # such as payments_faq.md, warranty_faq.docx, or delivery_faq.pdf.
        filename_aliases = {
            "payments": ("payment", "payments", "billing", "checkout"),
            "shipping": ("shipping", "shipment", "delivery", "tracking", "dispatch"),
            "refunds": ("refund", "refunds", "reimbursement"),
            "returns": ("return", "returns", "exchange"),
            "warranty": ("warranty", "warranties", "guarantee"),
        }

        for category, aliases in filename_aliases.items():
            if any(alias in filename_only for alias in aliases):
                scores[category] += 30

        for category, terms in patterns.items():
            for term, weight in terms.items():
                # Count each signal once so long documents do not overwhelm
                # the filename signal merely by repeating a common word.
                if term in haystack:
                    scores[category] += weight

        return scores

    def detect_category_from_file(self, file_bytes: bytes, filename: str) -> str:
        """Extract a file and automatically determine its policy category."""
        safe_filename = self.sanitize_filename(filename)
        content = self._extract_text(file_bytes, safe_filename)
        return self.detect_category(content, safe_filename)

    @classmethod
    def detect_category(cls, content: str, filename: str) -> str:
        """Automatically detect the most likely RetailAssist policy category."""
        scores = cls._category_scores(content, filename)
        best_category = max(scores, key=scores.get)
        best_score = scores[best_category]

        if best_score <= 0:
            raise ValueError(
                f"Could not determine a policy category for {filename}. "
                "Please use a document containing clear retail policy/FAQ content."
            )

        return best_category

    @classmethod
    def _normalize_category(
        cls,
        category: str | None,
        filename: str,
        content: str | None = None,
    ) -> str:
        """Return an explicit category or automatically detect it from the document."""
        if category and category.strip():
            # Backward compatibility: callers may still provide a category.
            # The new UI does not provide one, so normal uploads are automatic.
            return category.strip().lower()

        if content is not None:
            return cls.detect_category(content, filename)

        # A filename-only fallback keeps the method usable by older callers.
        # upload_to_stage always performs content-based detection before indexing.
        filename_scores = cls._category_scores("", filename)
        best_category = max(filename_scores, key=filename_scores.get)
        if filename_scores[best_category] > 0:
            return best_category

        return "general"

    @staticmethod
    def _calculate_content_hash(file_bytes: bytes) -> str:
        return hashlib.sha256(file_bytes).hexdigest()

    @staticmethod
    def _is_question_line(line: str) -> bool:
        """
        Detect a customer FAQ question without requiring a predefined category.
        """
        value = line.strip()
        value = re.sub(r"^#{1,6}\s+", "", value).strip()
        return value.endswith("?") and 3 <= len(value) <= 300

    @classmethod
    def _build_chunks(cls, content: str) -> list[str]:
        """
        Create small, retrieval-focused chunks.

        If the document contains explicit questions:
            question + all following answer text
        becomes one chunk until the next question.

        This is the key fix for PDF/DOCX files containing many FAQs.
        """

        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if line.strip()
        ]

        if not lines:
            return []

        question_indexes = [
            index for index, line in enumerate(lines) if cls._is_question_line(line)
        ]

        chunks: list[str] = []

        if question_indexes:
            # Preserve each FAQ as one question + answer chunk.
            for position, start in enumerate(question_indexes):
                end = (
                    question_indexes[position + 1]
                    if position + 1 < len(question_indexes)
                    else len(lines)
                )

                chunk = "\n".join(lines[start:end]).strip()

                if chunk:
                    chunks.append(chunk)

            return chunks

        # No explicit FAQ questions. Fall back to paragraph/section chunks.
        paragraphs = re.split(r"\n\s*\n+", content.strip())

        current: list[str] = []
        current_length = 0
        max_chars = 1200

        for paragraph in paragraphs:
            paragraph = re.sub(r"\s+", " ", paragraph).strip()
            if not paragraph:
                continue

            if current and current_length + len(paragraph) + 2 > max_chars:
                chunks.append("\n\n".join(current).strip())
                current = []
                current_length = 0

            current.append(paragraph)
            current_length += len(paragraph) + 2

        if current:
            chunks.append("\n\n".join(current).strip())

        return chunks

    def _ingest_to_policy_tables(
        self,
        document_id: str,
        filename: str,
        category: str,
        content: str,
    ) -> int:
        if not content.strip():
            raise ValueError(f"{filename} does not contain readable text.")

        chunks = self._build_chunks(content)

        if not chunks:
            raise ValueError(f"{filename} did not produce searchable chunks.")

        safe_document_id = document_id.replace("'", "''")
        safe_filename = filename.replace("'", "''")
        safe_category = category.replace("'", "''")
        safe_content = content.replace("'", "''")

        # Remove old chunks for this document before rebuilding them.
        self.session.sql(
            f"""
            DELETE FROM {POLICY_CHUNKS_TABLE}
            WHERE DOCUMENT_ID = '{safe_document_id}'
            """
        ).collect()

        # Keep one source record per document.
        self.session.sql(
            f"""
            MERGE INTO {POLICY_SOURCES_TABLE} AS target
            USING (
                SELECT
                    '{safe_document_id}' AS DOCUMENT_ID,
                    '{safe_filename}' AS DOCUMENT_NAME,
                    '{safe_category}' AS CATEGORY,
                    '{safe_content}' AS CONTENT
            ) AS source
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

        # Insert each retrieval-focused chunk separately.
        for index, chunk in enumerate(chunks):
            chunk_id = f"{document_id}_CHUNK_{index + 1:04d}"

            safe_chunk_id = chunk_id.replace("'", "''")
            safe_chunk = chunk.replace("'", "''")

            self.session.sql(
                f"""
                INSERT INTO {POLICY_CHUNKS_TABLE} (
                    CHUNK_ID,
                    DOCUMENT_ID,
                    DOCUMENT_NAME,
                    CATEGORY,
                    CHUNK_INDEX,
                    CHUNK_TEXT,
                    CHUNK_LENGTH
                )
                VALUES (
                    '{safe_chunk_id}',
                    '{safe_document_id}',
                    '{safe_filename}',
                    '{safe_category}',
                    {index},
                    '{safe_chunk}',
                    {len(chunk)}
                )
                """
            ).collect()

        return len(chunks)

    def upload_to_stage(
        self,
        file_bytes: bytes,
        filename: str,
        document_id: str,
        category: str | None = None,
    ) -> str:

        safe_filename = self.sanitize_filename(filename)

        # Extract once before creating Snowflake metadata so the category is
        # determined independently for every uploaded file.
        content = self._extract_text(
            file_bytes,
            safe_filename,
        )
        normalized_category = self._normalize_category(
            category,
            safe_filename,
            content=content,
        )

        content_hash = self._calculate_content_hash(file_bytes)
        safe_hash = content_hash.replace("'", "''")

        # Prevent duplicate content uploads.
        existing = self.session.sql(
            f"""
            SELECT DOCUMENT_ID, ORIGINAL_FILENAME
            FROM {DOCUMENTS_TABLE}
            WHERE CONTENT_HASH = '{safe_hash}'
              AND ACTIVE = TRUE
            LIMIT 1
            """
        ).collect()

        if existing:
            row = existing[0]
            raise ValueError(
                f"Document already exists: "
                f"{row['ORIGINAL_FILENAME']} "
                f"(Document ID: {row['DOCUMENT_ID']})"
            )

        file_type = os.path.splitext(safe_filename)[1].lower().lstrip(".")
        file_size = len(file_bytes)
        stage_path = f"@{STAGE_NAME}/{document_id}"

        safe_document_id = document_id.replace("'", "''")
        safe_original = filename.replace("'", "''")
        safe_filename_sql = safe_filename.replace("'", "''")
        safe_category = normalized_category.replace("'", "''")
        safe_stage_path = (f"{stage_path}/{safe_filename}").replace("'", "''")

        # Register document before processing.
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
                PROCESSING_STATUS,
                ACTIVE
            )
            VALUES (
                '{safe_document_id}',
                '{safe_original}',
                '{safe_filename_sql}',
                '{safe_stage_path}',
                '{file_type}',
                {file_size},
                '{safe_hash}',
                '{safe_category}',
                'UPLOADING',
                TRUE
            )
            """
        ).collect()

        try:
            # Store original file.
            self.session.file.put_stream(
                io.BytesIO(file_bytes),
                stage_path,
                auto_compress=False,
                overwrite=False,
            )

            chunk_count = self._ingest_to_policy_tables(
                document_id=document_id,
                filename=safe_filename,
                category=normalized_category,
                content=content,
            )

            page_count = 0
            if file_type == "pdf":
                try:
                    from pypdf import PdfReader

                    page_count = len(PdfReader(io.BytesIO(file_bytes)).pages)
                except Exception:
                    page_count = 0

            character_count = len(content)

            self.session.sql(
                f"""
                UPDATE {DOCUMENTS_TABLE}
                SET
                    PROCESSING_STATUS = 'INDEXED',
                    PAGE_COUNT = {page_count},
                    CHARACTER_COUNT = {character_count},
                    CHUNK_COUNT = {chunk_count},
                    UPDATED_AT = CURRENT_TIMESTAMP(),
                    ERROR_MESSAGE = NULL
                WHERE DOCUMENT_ID = '{safe_document_id}'
                """
            ).collect()

        except Exception as exc:
            error_message = str(exc).replace("'", "''")

            self.session.sql(
                f"""
                UPDATE {DOCUMENTS_TABLE}
                SET
                    PROCESSING_STATUS = 'FAILED',
                    ERROR_MESSAGE = '{error_message}',
                    UPDATED_AT = CURRENT_TIMESTAMP()
                WHERE DOCUMENT_ID = '{safe_document_id}'
                """
            ).collect()

            raise

        return f"{stage_path}/{safe_filename}"
