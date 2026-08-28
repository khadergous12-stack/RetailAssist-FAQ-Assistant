from __future__ import annotations

import json
from typing import Any

from rag.contracts import RetrievedChunk
from providers.snowflake.connection import create_snowflake_session


DOCUMENTS_TABLE = "RETAIL_ASSIST_DB.RETAIL_ASSIST.DOCUMENTS"


class SnowflakeRetriever:
    """
    Cortex Search candidate retriever.

    This class intentionally does NOT decide that the first document wins and
    it does NOT return every chunk from the first document.  Cortex Search
    returns a broad candidate pool; RAGService performs the final question-
    specific evidence selection.

    Lifecycle rules are still enforced here:
      * ACTIVE + INDEXED uploaded documents are allowed.
      * deleted/failed/processing uploaded documents are excluded.
      * legacy built-in rows that are not present in DOCUMENTS remain allowed.
    """

    def __init__(
        self,
        session=None,
        search_service="RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH",
        top_k=5,
    ):
        self.session = session or create_snowflake_session()
        self.search_service = search_service
        self.top_k = top_k

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"true", "1", "yes", "y"}

    @staticmethod
    def _score(item: dict[str, Any]) -> float | None:
        scores = item.get("@scores") or item.get("scores") or {}
        for key in ("reranker_score", "cosine_similarity", "score"):
            value = scores.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
        value = item.get("score")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _page_number(value: Any) -> int | None:
        if value is None or str(value).strip() == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _document_states(self, document_ids: set[str]) -> dict[str, dict[str, Any]]:
        if not document_ids:
            return {}

        ids = ", ".join(
            "'" + str(doc_id).replace("'", "''") + "'" for doc_id in document_ids
        )

        rows = self.session.sql(
            f"""
            SELECT DOCUMENT_ID, ACTIVE, PROCESSING_STATUS
            FROM {DOCUMENTS_TABLE}
            WHERE DOCUMENT_ID IN ({ids})
            """
        ).collect()

        states: dict[str, dict[str, Any]] = {}
        for row in rows:
            doc_id = str(row["DOCUMENT_ID"]).strip()
            states[doc_id] = {
                "known": True,
                "active": self._as_bool(row["ACTIVE"]),
                "status": str(row["PROCESSING_STATUS"] or "").upper(),
            }
        return states

    def _search(self, query: str, candidate_limit: int) -> list[RetrievedChunk]:
        request = {
            "query": query.strip(),
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT",
                "PAGE_INDEX",
                "PAGE_NUMBER",
                "SECTION_HEADING",
                "SOURCE_TYPE",
                "ACTIVE",
            ],
            "limit": candidate_limit,
        }

        request_json = json.dumps(request).replace("'", "''")
        sql = f"""
        SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
            '{self.search_service}',
            $$ {request_json} $$
        ) AS SEARCH_RESULT
        """

        rows = self.session.sql(sql).collect()
        if not rows:
            return []

        raw = rows[0]["SEARCH_RESULT"]
        if isinstance(raw, str):
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                return []
        else:
            result = raw

        if not isinstance(result, dict):
            return []

        raw_results = result.get("results", [])
        if not isinstance(raw_results, list):
            return []

        candidates: list[RetrievedChunk] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue

            chunk_text = str(item.get("CHUNK_TEXT", "") or "").strip()
            document_id = str(item.get("DOCUMENT_ID", "") or "").strip()
            if not chunk_text or not document_id:
                continue

            try:
                chunk_index = int(item.get("CHUNK_INDEX", 0) or 0)
            except (TypeError, ValueError):
                chunk_index = 0

            candidates.append(
                RetrievedChunk(
                    chunk_id=str(item.get("CHUNK_ID", "") or ""),
                    document_id=document_id,
                    document_name=str(item.get("DOCUMENT_NAME", "") or ""),
                    category=str(item.get("CATEGORY", "") or "").strip(),
                    chunk_index=chunk_index,
                    chunk_text=chunk_text,
                    score=self._score(item),
                    page_number=self._page_number(item.get("PAGE_NUMBER")),
                    section_heading=str(item.get("SECTION_HEADING", "") or ""),
                    source_type=str(item.get("SOURCE_TYPE", "") or ""),
                )
            )

        return candidates

    def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievedChunk]:
        if not query or not query.strip():
            return []

        limit = top_k if top_k is not None else self.top_k
        if limit <= 0:
            return []

        # IMPORTANT: ask Cortex for enough candidates to let RAGService compare
        # different documents and different sections.  Do not collapse by
        # document here.
        candidate_limit = max(30, limit * 8)
        candidates = self._search(query, candidate_limit)
        if not candidates:
            return []

        states = self._document_states({c.document_id for c in candidates})

        allowed: list[RetrievedChunk] = []
        for chunk in candidates:
            state = states.get(chunk.document_id)

            # If DOCUMENTS knows this ID, it is an uploaded document and must
            # be active + indexed.  This is the authoritative lifecycle rule.
            if state is not None:
                if not state["active"] or state["status"] != "INDEXED":
                    continue
                # The DOCUMENTS table is authoritative for source identity.
                # Do not trust SOURCE_TYPE returned by old/stale search rows.
                chunk.source_type = "USER_UPLOAD"
            else:
                # Legacy/built-in policy rows are not in DOCUMENTS.
                chunk.source_type = "BUILT_IN"

            allowed.append(chunk)

        # Preserve Cortex order.  The RAG service will score and filter these
        # candidates; this method must not pre-select a document or a chunk.
        return allowed
