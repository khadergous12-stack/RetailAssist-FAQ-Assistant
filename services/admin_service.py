import logging
import time

from providers.snowflake.connection import create_snowflake_session


logger = logging.getLogger(__name__)


class AdminService:
    """Administrator operations for SupportAI user accounts."""

    TABLE_NAME = "SUPPORT_USERS"

    def __init__(self, session=None):
        self.session = session
        self._owns_session = session is None

    def _get_session(self):
        if self.session is None:
            self.session = create_snowflake_session()
        return self.session

    @staticmethod
    def _row_to_dict(row) -> dict:
        """Convert a Snowpark Row into the public user dictionary."""
        return {
            "user_id": row["USER_ID"],
            "name": row["NAME"],
            "email": row["EMAIL"],
            "role": str(row["ROLE"]).upper(),
            "status": str(row["STATUS"]).upper(),
            "created_at": row["CREATED_AT"],
            "last_login": row["LAST_LOGIN"],
        }

    def list_users(self) -> list[dict]:
        """Return all SupportAI accounts for administrator review."""
        started = time.perf_counter()
        logger.info("Admin user listing started")

        try:
            session = self._get_session()
            rows = session.sql(
                f"""
                SELECT
                    USER_ID,
                    NAME,
                    EMAIL,
                    ROLE,
                    STATUS,
                    CREATED_AT,
                    LAST_LOGIN
                FROM {self.TABLE_NAME}
                ORDER BY CREATED_AT DESC
                """
            ).collect()

            users = [self._row_to_dict(row) for row in rows]

            logger.info(
                "Admin user listing completed | users=%s | duration=%.3fs",
                len(users),
                time.perf_counter() - started,
            )
            return users

        except Exception:
            logger.exception(
                "Admin user listing failed | duration=%.3fs",
                time.perf_counter() - started,
            )
            raise

    def get_user(self, user_id: str) -> dict | None:
        """Return one account by user ID."""
        started = time.perf_counter()
        user_id = str(user_id or "").strip()

        if not user_id:
            logger.warning(
                "Admin get-user rejected | reason=missing_user_id | duration=%.3fs",
                time.perf_counter() - started,
            )
            return None

        logger.info("Admin get-user started | user_id=%s", user_id)

        try:
            session = self._get_session()
            rows = session.sql(
                f"""
                SELECT
                    USER_ID,
                    NAME,
                    EMAIL,
                    ROLE,
                    STATUS,
                    CREATED_AT,
                    LAST_LOGIN
                FROM {self.TABLE_NAME}
                WHERE USER_ID = ?
                LIMIT 1
                """,
                params=[user_id],
            ).collect()

            if not rows:
                logger.warning(
                    "Admin get-user completed | outcome=NOT_FOUND | user_id=%s | duration=%.3fs",
                    user_id,
                    time.perf_counter() - started,
                )
                return None

            user = self._row_to_dict(rows[0])

            logger.info(
                "Admin get-user completed | outcome=SUCCESS | user_id=%s | role=%s | status=%s | duration=%.3fs",
                user_id,
                user["role"],
                user["status"],
                time.perf_counter() - started,
            )
            return user

        except Exception:
            logger.exception(
                "Admin get-user failed | user_id=%s | duration=%.3fs",
                user_id,
                time.perf_counter() - started,
            )
            raise

    def set_user_status(self, user_id: str, status: str) -> bool:
        """Set a USER account to ACTIVE or INACTIVE."""
        started = time.perf_counter()
        user_id = str(user_id or "").strip()
        requested_status = str(status or "").strip().upper()

        logger.info(
            "Admin status change started | user_id=%s | requested_status=%s",
            user_id or "<missing>",
            requested_status or "<missing>",
        )

        if not user_id:
            logger.warning(
                "Admin status change rejected | reason=missing_user_id | duration=%.3fs",
                time.perf_counter() - started,
            )
            raise ValueError("User ID is required.")

        if requested_status not in {"ACTIVE", "INACTIVE"}:
            logger.warning(
                "Admin status change rejected | reason=invalid_status | user_id=%s | status=%s | duration=%.3fs",
                user_id,
                requested_status,
                time.perf_counter() - started,
            )
            raise ValueError("Status must be ACTIVE or INACTIVE.")

        try:
            session = self._get_session()

            rows = session.sql(
                f"""
                SELECT USER_ID, ROLE, STATUS
                FROM {self.TABLE_NAME}
                WHERE USER_ID = ?
                LIMIT 1
                """,
                params=[user_id],
            ).collect()

            if not rows:
                logger.warning(
                    "Admin status change rejected | reason=user_not_found | user_id=%s | duration=%.3fs",
                    user_id,
                    time.perf_counter() - started,
                )
                return False

            current_role = str(rows[0]["ROLE"]).upper()
            current_status = str(rows[0]["STATUS"]).upper()

            if current_role != "USER":
                logger.warning(
                    "Admin status change rejected | reason=admin_account_protected | user_id=%s | duration=%.3fs",
                    user_id,
                    time.perf_counter() - started,
                )
                raise ValueError(
                    "Administrator accounts cannot be activated or deactivated here."
                )

            if current_status == requested_status:
                logger.info(
                    "Admin status change completed | outcome=NO_CHANGE | user_id=%s | status=%s | duration=%.3fs",
                    user_id,
                    requested_status,
                    time.perf_counter() - started,
                )
                return True

            session.sql(
                f"""
                UPDATE {self.TABLE_NAME}
                SET STATUS = ?
                WHERE USER_ID = ?
                  AND UPPER(ROLE) = 'USER'
                """,
                params=[requested_status, user_id],
            ).collect()

            logger.info(
                "Admin status change completed | outcome=SUCCESS | user_id=%s | from=%s | to=%s | duration=%.3fs",
                user_id,
                current_status,
                requested_status,
                time.perf_counter() - started,
            )
            return True

        except ValueError:
            raise
        except Exception:
            logger.exception(
                "Admin status change failed | user_id=%s | requested_status=%s | duration=%.3fs",
                user_id,
                requested_status,
                time.perf_counter() - started,
            )
            raise

    def activate_user(self, user_id: str) -> bool:
        """Activate a normal USER account."""
        logger.info("Admin activate-user requested | user_id=%s", user_id)
        return self.set_user_status(user_id, "ACTIVE")

    def deactivate_user(self, user_id: str) -> bool:
        """Deactivate a normal USER account."""
        logger.info("Admin deactivate-user requested | user_id=%s", user_id)
        return self.set_user_status(user_id, "INACTIVE")

    def get_user_stats(self) -> dict:
        """Return account counts used by the administrator dashboard."""
        started = time.perf_counter()
        logger.info("Admin user statistics started")

        try:
            session = self._get_session()
            rows = session.sql(
                f"""
                SELECT
                    COUNT(*) AS TOTAL_USERS,
                    COUNT_IF(UPPER(ROLE) = 'USER') AS TOTAL_USER_ACCOUNTS,
                    COUNT_IF(UPPER(ROLE) = 'ADMIN') AS TOTAL_ADMINS,
                    COUNT_IF(UPPER(STATUS) = 'ACTIVE') AS ACTIVE_USERS,
                    COUNT_IF(UPPER(STATUS) = 'INACTIVE') AS INACTIVE_USERS
                FROM {self.TABLE_NAME}
                """
            ).collect()

            row = rows[0]

            stats = {
                "total_users": int(row["TOTAL_USERS"] or 0),
                "total_user_accounts": int(row["TOTAL_USER_ACCOUNTS"] or 0),
                "total_admins": int(row["TOTAL_ADMINS"] or 0),
                "active_users": int(row["ACTIVE_USERS"] or 0),
                "inactive_users": int(row["INACTIVE_USERS"] or 0),
            }

            logger.info(
                "Admin user statistics completed | total=%s | users=%s | admins=%s | active=%s | inactive=%s | duration=%.3fs",
                stats["total_users"],
                stats["total_user_accounts"],
                stats["total_admins"],
                stats["active_users"],
                stats["inactive_users"],
                time.perf_counter() - started,
            )
            return stats

        except Exception:
            logger.exception(
                "Admin user statistics failed | duration=%.3fs",
                time.perf_counter() - started,
            )
            raise

    def close(self) -> None:
        """Close the session only if AdminService created it."""
        if self._owns_session and self.session is not None:
            try:
                self.session.close()
            finally:
                self.session = None
