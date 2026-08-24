import json

from rag.contracts import RetrievedChunk, Retriever

from providers.snowflake.connection import create_snowflake_session


class SnowflakeRetriever:
    """
    Snowflake Cortex Search implementation.

    Retrieves candidate policy chunks and applies category filtering
    when the customer question clearly maps to one FAQ category.

    Final relevance validation is handled by RAGService.
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
        ],
        "shipping": [
            "shipping",
            "delivery",
            "delivered",
            "shipment",
            "dispatch",
            "courier",
            "arrive",
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
        ],
        "payments": [
            "card",
            "payment",
            "declined",
            "charge",
            "charged",
            "checkout",
            "transaction",
            "payment failed",
            "cryptocurrency",
        ],
    }

    def __init__(
        self,
        session=None,
        search_service=("RETAIL_ASSIST_DB.RETAIL_ASSIST.RETAIL_ASSIST_SEARCH"),
        top_k: int = 5,
    ):
        self.session = session or create_snowflake_session()
        self.search_service = search_service
        self.top_k = top_k

    def _detect_category(
        self,
        query: str,
    ) -> str | None:
        """
        Detect an obvious FAQ category.

        A category is only applied when exactly one category
        matches the customer's question.
        """

        query_lower = query.lower()

        matches = []

        for category, keywords in self.CATEGORY_KEYWORDS.items():
            for keyword in keywords:
                if keyword in query_lower:
                    matches.append(category)
                    break

        if len(matches) == 1:
            return matches[0]

        return None

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """
        Retrieve candidate chunks from Cortex Search.

        Relevance validation is intentionally NOT done here.
        RAGService performs the final evidence validation so
        unsupported questions do not expose irrelevant sources.
        """

        limit = top_k if top_k is not None else self.top_k

        category = self._detect_category(query)

        search_request = {
            "query": query,
            "columns": [
                "CHUNK_ID",
                "DOCUMENT_ID",
                "DOCUMENT_NAME",
                "CATEGORY",
                "CHUNK_INDEX",
                "CHUNK_TEXT",
            ],
            "limit": limit,
        }

        # ---------------------------------------------------------
        # Restrict retrieval to the relevant FAQ category when
        # exactly one category is clearly identified.
        # ---------------------------------------------------------
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

        results = result.get("results", [])

        chunks: list[RetrievedChunk] = []

        for item in results:
            chunk_text = str(item.get("CHUNK_TEXT", "")).strip()

            if not chunk_text:
                continue

            raw_chunk_index = item.get(
                "CHUNK_INDEX",
                0,
            )

            try:
                chunk_index = int(raw_chunk_index)
            except (TypeError, ValueError):
                chunk_index = 0

            raw_score = item.get("score")

            try:
                score = float(raw_score) if raw_score is not None else None
            except (TypeError, ValueError):
                score = None

            chunks.append(
                RetrievedChunk(
                    chunk_id=str(item.get("CHUNK_ID", "")),
                    document_id=str(item.get("DOCUMENT_ID", "")),
                    document_name=str(item.get("DOCUMENT_NAME", "")),
                    category=str(item.get("CATEGORY", "")),
                    chunk_index=chunk_index,
                    chunk_text=chunk_text,
                    score=score,
                )
            )

        return chunks
