from providers.snowflake.connection import create_snowflake_session

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()


@dataclass
class CortexAnalystResponse:
    """Result returned by Cortex Analyst."""

    answer: str
    sql: Optional[str] = None
    result: Optional[str] = None
    request_id: Optional[str] = None


class CortexAnalystService:
    """
    Service responsible for communicating with Snowflake Cortex Analyst.

    This service is intentionally separate from the existing RAG/provider
    generation architecture.
    """

    def __init__(
        self,
        semantic_view: str = "RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_ANALYTICS",
    ):
        start = time.perf_counter()
        self.semantic_view = semantic_view
        self.account = os.environ.get("SNOWFLAKE_ACCOUNT")
        self.pat = os.environ.get("SNOWFLAKE_PAT")
        if not self.account:
            logger.error(
                "Cortex Analyst initialization failed | reason=missing_account"
            )
            raise ValueError(
                "Missing Snowflake environment variable: SNOWFLAKE_ACCOUNT"
            )
        if not self.pat:
            logger.error("Cortex Analyst initialization failed | reason=missing_pat")
            raise ValueError("Missing Snowflake environment variable: SNOWFLAKE_PAT")

        # Cortex Analyst REST API endpoint.
        self.host = os.environ.get(
            "SNOWFLAKE_HOST",
            f"{self.account}.snowflakecomputing.com",
        )
        self.url = f"https://{self.host}/api/v2/cortex/analyst/message"
        logger.info(
            "Cortex Analyst service initialized | semantic_view=%s | duration=%.3fs",
            self.semantic_view,
            time.perf_counter() - start,
        )

    def ask(self, question: str) -> CortexAnalystResponse:
        """
        Send a natural-language question to Cortex Analyst.
        """
        start_time = time.perf_counter()

        if not question or not question.strip():
            logger.warning(
                "Cortex Analyst request rejected | reason=empty_question | duration=%.3fs",
                time.perf_counter() - start_time,
            )
            raise ValueError("Question cannot be empty.")

        clean_question = question.strip()
        logger.info(
            "Cortex Analyst request started | semantic_view=%s | question_length=%s",
            self.semantic_view,
            len(clean_question),
        )

        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": clean_question}],
                }
            ],
            "semantic_view": self.semantic_view,
        }
        headers = {
            "Authorization": f"Bearer {self.pat}",
            "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
            "Content-Type": "application/json",
        }

        request_start = time.perf_counter()
        try:
            response = requests.post(
                self.url,
                json=payload,
                headers=headers,
                timeout=60,
            )
        except requests.RequestException as exc:
            duration = time.perf_counter() - start_time
            logger.exception(
                "Cortex Analyst API request failed | exception=%s | duration=%.3fs",
                type(exc).__name__,
                duration,
            )
            raise RuntimeError(
                "Unable to connect to Snowflake Cortex Analyst."
            ) from exc

        request_duration = time.perf_counter() - request_start
        request_id = response.headers.get("X-Snowflake-Request-Id")

        logger.info(
            "Cortex Analyst API response received | status=%s | request_id=%s | duration=%.3fs",
            response.status_code,
            request_id or "none",
            request_duration,
        )

        if response.status_code >= 400:
            logger.error(
                "Cortex Analyst request rejected | status=%s | request_id=%s | duration=%.3fs",
                response.status_code,
                request_id or "none",
                time.perf_counter() - start_time,
            )
            try:
                error_body = response.json()
            except ValueError:
                error_body = {}
            message = (
                error_body.get("message")
                or error_body.get("error")
                or "Cortex Analyst request failed."
            )
            raise RuntimeError(f"Cortex Analyst request failed: {message}")

        try:
            data = response.json()
        except ValueError as exc:
            logger.exception(
                "Cortex Analyst response parsing failed | exception=%s | duration=%.3fs",
                type(exc).__name__,
                time.perf_counter() - start_time,
            )
            raise RuntimeError("Cortex Analyst returned invalid JSON.") from exc

        answer_parts = []
        generated_sql = None
        messages = data.get("message", {}).get("content", [])

        for content_block in messages:
            content_type = content_block.get("type")
            if content_type == "text":
                text = content_block.get("text")
                if text:
                    answer_parts.append(text)
            elif content_type == "sql":
                generated_sql = content_block.get("statement")

        answer = "\n".join(answer_parts).strip()
        logger.info(
            "Cortex Analyst response parsed | answer_length=%s | sql_generated=%s",
            len(answer),
            bool(generated_sql),
        )

        result = None
        if generated_sql:
            # Cortex Analyst may append a generated-by comment after the SQL.
            clean_sql = generated_sql.split("-- Generated by Cortex Analyst")[0].strip()

            sql_start = time.perf_counter()
            logger.info(
                "Cortex Analyst SQL execution started | request_id=%s",
                request_id or "none",
            )
            try:
                session = create_snowflake_session()
                try:
                    rows = session.sql(clean_sql).collect()
                    result = [row.as_dict() for row in rows]
                finally:
                    session.close()

                logger.info(
                    "Cortex Analyst SQL execution completed | rows=%s | duration=%.3fs",
                    len(result),
                    time.perf_counter() - sql_start,
                )
            except Exception as exc:
                logger.exception(
                    "Cortex Analyst SQL execution failed | exception=%s | duration=%.3fs",
                    type(exc).__name__,
                    time.perf_counter() - sql_start,
                )
                raise RuntimeError(
                    f"Cortex Analyst generated SQL could not be executed: {exc}"
                ) from exc
        else:
            logger.info("Cortex Analyst returned no generated SQL.")

        total_duration = time.perf_counter() - start_time
        if total_duration >= 10:
            logger.warning(
                "Slow Cortex Analyst request | duration=%.3fs | request_id=%s",
                total_duration,
                request_id or "none",
            )

        logger.info(
            "Cortex Analyst request completed | duration=%.3fs | answer_length=%s | sql_generated=%s | result_rows=%s",
            total_duration,
            len(answer),
            bool(generated_sql),
            len(result) if result is not None else 0,
        )

        return CortexAnalystResponse(
            answer=answer,
            sql=generated_sql,
            result=result,
            request_id=request_id,
        )
