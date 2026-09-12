from __future__ import annotations

import logging
import uuid
from typing import Any

from providers.snowflake.connection import create_snowflake_session

logger = logging.getLogger(__name__)


class ActivityService:
    """Persist and retrieve lightweight SupportAI usage/activity events."""

    TABLE_NAME = "RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_ACTIVITY_LOG"

    def __init__(self, session=None):
        self.session = session
        self._owns_session = session is None

        if self.session is None:
            self.session = create_snowflake_session()
            self._owns_session = True

    def log_activity(
        self,
        user_id: str,
        activity_type: str,
        mode: str | None = None,
        status: str = "SUCCESS",
        latency_seconds: float | None = None,
    ) -> None:
        if not user_id:
            return

        normalized_type = str(activity_type).strip().upper()
        normalized_mode = str(mode).strip() if mode else None
        normalized_status = str(status).strip().upper() or "SUCCESS"

        if not normalized_type:
            return

        self.session.sql(
            f"""
            INSERT INTO {self.TABLE_NAME}
            (
                ACTIVITY_ID,
                USER_ID,
                ACTIVITY_TYPE,
                MODE,
                STATUS,
                LATENCY_SECONDS
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            params=[
                str(uuid.uuid4()),
                str(user_id),
                normalized_type,
                normalized_mode,
                normalized_status,
                float(latency_seconds) if latency_seconds is not None else None,
            ],
        ).collect()

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        return {str(key).lower(): value for key, value in row.as_dict().items()}

    def get_usage_summary(self) -> dict[str, Any]:
        rows = self.session.sql(
            f"""
            SELECT
                COUNT(*) AS TOTAL_ACTIVITY,
                COUNT_IF(ACTIVITY_TYPE = 'QUERY') AS TOTAL_QUERIES,
                COUNT_IF(ACTIVITY_TYPE = 'QUERY' AND MODE = 'Policy RAG') AS POLICY_QUERIES,
                COUNT_IF(ACTIVITY_TYPE = 'QUERY' AND MODE = 'Support Analytics') AS ANALYTICS_QUERIES,
                COUNT_IF(ACTIVITY_TYPE = 'QUERY' AND MODE = 'Cortex Agent') AS AGENT_QUERIES,
                COUNT_IF(ACTIVITY_TYPE = 'LOGIN') AS TOTAL_LOGINS,
                COUNT(DISTINCT USER_ID) AS ACTIVE_USERS
            FROM {self.TABLE_NAME}
            WHERE CREATED_AT >= DATEADD(day, -7, CURRENT_TIMESTAMP())
            """
        ).collect()

        if not rows:
            return {
                "total_activity": 0,
                "total_queries": 0,
                "policy_queries": 0,
                "analytics_queries": 0,
                "agent_queries": 0,
                "total_logins": 0,
                "active_users": 0,
            }

        row = self._row_to_dict(rows[0])
        return {
            "total_activity": int(row.get("total_activity") or 0),
            "total_queries": int(row.get("total_queries") or 0),
            "policy_queries": int(row.get("policy_queries") or 0),
            "analytics_queries": int(row.get("analytics_queries") or 0),
            "agent_queries": int(row.get("agent_queries") or 0),
            "total_logins": int(row.get("total_logins") or 0),
            "active_users": int(row.get("active_users") or 0),
        }

    def list_recent_activity(self, limit: int = 25) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        rows = self.session.sql(
            f"""
            SELECT
                A.CREATED_AT,
                U.NAME,
                U.EMAIL,
                A.ACTIVITY_TYPE,
                A.MODE,
                A.STATUS,
                A.LATENCY_SECONDS
            FROM {self.TABLE_NAME} A
            LEFT JOIN RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_USERS U
                ON A.USER_ID = U.USER_ID
            ORDER BY A.CREATED_AT DESC
            LIMIT {safe_limit}
            """
        ).collect()
        return [self._row_to_dict(row) for row in rows]

    def list_user_usage(self, limit: int = 25) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        rows = self.session.sql(
            f"""
            SELECT
                U.NAME,
                U.EMAIL,
                COUNT(A.ACTIVITY_ID) AS ACTIVITY_COUNT,
                COUNT_IF(A.ACTIVITY_TYPE = 'LOGIN') AS LOGIN_COUNT,
                COUNT_IF(A.ACTIVITY_TYPE = 'QUERY' AND A.MODE = 'Policy RAG') AS POLICY_COUNT,
                COUNT_IF(A.ACTIVITY_TYPE = 'QUERY' AND A.MODE = 'Support Analytics') AS ANALYTICS_COUNT,
                COUNT_IF(A.ACTIVITY_TYPE = 'QUERY' AND A.MODE = 'Cortex Agent') AS AGENT_COUNT,
                MAX(A.CREATED_AT) AS LAST_ACTIVITY
            FROM RETAIL_ASSIST_DB.RETAIL_ASSIST.SUPPORT_USERS U
            LEFT JOIN {self.TABLE_NAME} A
                ON U.USER_ID = A.USER_ID
                AND A.CREATED_AT >= DATEADD(day, -7, CURRENT_TIMESTAMP())
            GROUP BY U.NAME, U.EMAIL
            ORDER BY ACTIVITY_COUNT DESC, LAST_ACTIVITY DESC NULLS LAST
            LIMIT {safe_limit}
            """
        ).collect()
        return [self._row_to_dict(row) for row in rows]

    def close(self) -> None:
        if self._owns_session and self.session is not None:
            try:
                self.session.close()
            finally:
                self.session = None
