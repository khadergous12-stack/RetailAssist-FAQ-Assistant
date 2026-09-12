import hashlib
import logging
import secrets
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from providers.snowflake.connection import create_snowflake_session
from services.activity_service import ActivityService


logger = logging.getLogger(__name__)


class AuthService:
    """Authentication and user-account management for SupportAI."""

    TABLE_NAME = "SUPPORT_USERS"
    RESET_TABLE_NAME = "SUPPORT_PASSWORD_RESET_REQUESTS"

    def __init__(self, session=None):
        self.session = session
        self._owns_session = session is None
        # Internal status from the most recent login attempt.
        # The UI uses this only to distinguish an inactive account from
        # invalid credentials while keeping the login return contract intact.
        self.last_login_status = None

    @staticmethod
    def _mask_email(email: str) -> str:
        """Return a log-safe representation of an email address."""
        value = (email or "").strip().lower()
        if not value:
            return "<empty>"
        if "@" not in value:
            return "<invalid>"
        local, domain = value.split("@", 1)
        if len(local) <= 2:
            masked_local = local[:1] + "*" * max(0, len(local) - 1)
        else:
            masked_local = local[:1] + "*" * (len(local) - 2) + local[-1:]
        return f"{masked_local}@{domain}"

    @contextmanager
    def _operation(self, operation: str, **fields):
        """Log an authentication operation with duration and failure details."""
        started = time.perf_counter()
        details = " | ".join(
            f"{key}={value}" for key, value in fields.items() if value not in (None, "")
        )
        suffix = f" | {details}" if details else ""
        logger.info("%s started%s", operation, suffix)

        outcome = "SUCCESS"
        reason = None
        try:
            tracker = {"outcome": "SUCCESS", "reason": None}
            yield tracker
            outcome = tracker["outcome"]
            reason = tracker["reason"]
        except Exception as exc:
            duration = time.perf_counter() - started
            logger.exception(
                "%s failed | duration=%.3fs | error=%s%s",
                operation,
                duration,
                type(exc).__name__,
                f" | {fields}" if False else "",
            )
            raise
        else:
            duration = time.perf_counter() - started
            if outcome == "REJECTED":
                logger.warning(
                    "%s rejected | reason=%s | duration=%.3fs",
                    operation,
                    reason or "unspecified",
                    duration,
                )
            else:
                logger.info(
                    "%s completed | outcome=%s | duration=%.3fs",
                    operation,
                    outcome,
                    duration,
                )

    def _get_session(self):
        if self.session is None:
            logger.info("Snowflake session creation started | service=AuthService")
            started = time.perf_counter()
            self.session = create_snowflake_session()
            logger.info(
                "Snowflake session ready | service=AuthService | duration=%.3fs",
                time.perf_counter() - started,
            )
        return self.session

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    @staticmethod
    def _hash_password(password: str, salt: str | None = None) -> str:
        """
        Hash a password using PBKDF2-HMAC-SHA256.

        Stored format:
            salt$iterations$hash
        """
        if salt is None:
            salt = secrets.token_hex(16)

        iterations = 600_000

        password_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        ).hex()

        return f"{salt}${iterations}${password_hash}"

    @staticmethod
    def _verify_password(password: str, stored_hash: str) -> bool:
        try:
            salt, iterations, expected_hash = stored_hash.split("$", 2)

            calculated_hash = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt.encode("utf-8"),
                int(iterations),
            ).hex()

            return secrets.compare_digest(
                calculated_hash,
                expected_hash,
            )

        except (ValueError, TypeError):
            logger.warning("Invalid stored password hash format.")
            return False

    @staticmethod
    def _validate_signup(name: str, email: str, password: str) -> None:
        if not name or not name.strip():
            raise ValueError("Name is required.")

        if not email or "@" not in email:
            raise ValueError("Please enter a valid email address.")

        if not password:
            raise ValueError("Password is required.")

        if len(password) < 8:
            raise ValueError("Password must contain at least 8 characters.")

    def signup(
        self,
        name: str,
        email: str,
        password: str,
    ) -> dict:
        """Create a normal USER account."""
        with self._operation(
            "Signup",
            role="USER",
            email=self._mask_email(email),
        ) as op:
            name = name.strip()
            email = self._normalize_email(email)

            try:
                self._validate_signup(name, email, password)
            except ValueError as exc:
                op["outcome"] = "REJECTED"
                op["reason"] = "validation"
                logger.warning(
                    "Signup validation rejected | reason=%s | email=%s",
                    str(exc),
                    self._mask_email(email),
                )
                raise

            session = self._get_session()

            existing = session.sql(
                f"""
                SELECT USER_ID
                FROM {self.TABLE_NAME}
                WHERE LOWER(EMAIL) = ?
                LIMIT 1
                """,
                params=[email],
            ).collect()

            if existing:
                op["outcome"] = "REJECTED"
                op["reason"] = "duplicate_email"
                logger.warning(
                    "Signup rejected | reason=duplicate_email | email=%s",
                    self._mask_email(email),
                )
                raise ValueError("An account with this email already exists.")

            user_id = str(uuid.uuid4())
            password_hash = self._hash_password(password)

            session.sql(
                f"""
                INSERT INTO {self.TABLE_NAME}
                (
                    USER_ID,
                    NAME,
                    EMAIL,
                    PASSWORD_HASH,
                    ROLE,
                    STATUS
                )
                VALUES (?, ?, ?, ?, 'USER', 'ACTIVE')
                """,
                params=[
                    user_id,
                    name,
                    email,
                    password_hash,
                ],
            ).collect()

            logger.info(
                "User account created successfully | user_id=%s | role=USER | status=ACTIVE",
                user_id,
            )

            return {
                "user_id": user_id,
                "name": name,
                "email": email,
                "role": "USER",
                "status": "ACTIVE",
            }

    def login(
        self,
        email: str,
        password: str,
    ) -> dict | None:
        """Authenticate an existing user."""
        with self._operation(
            "Login",
            email=self._mask_email(email),
        ) as op:
            self.last_login_status = None
            email = self._normalize_email(email)

            if not email or not password:
                self.last_login_status = "invalid_credentials"
                op["outcome"] = "REJECTED"
                op["reason"] = "missing_credentials"
                return None

            session = self._get_session()

            rows = session.sql(
                f"""
                SELECT
                    USER_ID,
                    NAME,
                    EMAIL,
                    PASSWORD_HASH,
                    ROLE,
                    STATUS
                FROM {self.TABLE_NAME}
                WHERE LOWER(EMAIL) = ?
                LIMIT 1
                """,
                params=[email],
            ).collect()

            if not rows:
                self.last_login_status = "account_not_found"
                op["outcome"] = "REJECTED"
                op["reason"] = "account_not_found"
                logger.warning("Login rejected | reason=account_not_found")
                return None

            row = rows[0]

            user_id = row["USER_ID"]
            name = row["NAME"]
            stored_email = row["EMAIL"]
            password_hash = row["PASSWORD_HASH"]
            role = str(row["ROLE"]).upper()
            status = str(row["STATUS"]).upper()

            if status != "ACTIVE":
                self.last_login_status = "inactive"
                op["outcome"] = "REJECTED"
                op["reason"] = "inactive_account"
                logger.warning(
                    "Login rejected | reason=inactive_account | user_id=%s | role=%s",
                    user_id,
                    role,
                )
                return None

            if not self._verify_password(password, password_hash):
                self.last_login_status = "invalid_credentials"
                op["outcome"] = "REJECTED"
                op["reason"] = "invalid_credentials"
                logger.warning(
                    "Login rejected | reason=invalid_credentials | user_id=%s | role=%s",
                    user_id,
                    role,
                )
                return None

            session.sql(
                f"""
                UPDATE {self.TABLE_NAME}
                SET LAST_LOGIN = CURRENT_TIMESTAMP()
                WHERE USER_ID = ?
                """,
                params=[user_id],
            ).collect()

            self.last_login_status = "success"

            try:
                ActivityService(session=session).log_activity(
                    user_id=user_id,
                    activity_type="LOGIN",
                )
            except Exception:
                logger.exception("Login activity logging failed | user_id=%s", user_id)

            logger.info(
                "User login successful | user_id=%s | role=%s | status=%s",
                user_id,
                role,
                status,
            )

            return {
                "user_id": user_id,
                "name": name,
                "email": stored_email,
                "role": role,
                "status": status,
            }

    def request_password_reset(self, email: str) -> bool:
        """Create a password-reset request without revealing account existence."""
        with self._operation(
            "Password reset request",
            email=self._mask_email(email),
        ) as op:
            email = self._normalize_email(email)
            if not email or "@" not in email:
                op["outcome"] = "REJECTED"
                op["reason"] = "invalid_email"
                raise ValueError("Please enter a valid email address.")

            session = self._get_session()

            rows = session.sql(
                f"""
                SELECT USER_ID
                FROM {self.TABLE_NAME}
                WHERE LOWER(EMAIL) = ?
                  AND UPPER(ROLE) = 'USER'
                  AND UPPER(STATUS) = 'ACTIVE'
                LIMIT 1
                """,
                params=[email],
            ).collect()

            if rows:
                user_id = rows[0]["USER_ID"]
                reset_id = str(uuid.uuid4())
                session.sql(
                    f"""
                    INSERT INTO {self.RESET_TABLE_NAME}
                    (RESET_ID, USER_ID, EMAIL, STATUS)
                    VALUES (?, ?, ?, 'PENDING')
                    """,
                    params=[reset_id, user_id, email],
                ).collect()
                logger.info(
                    "Password reset request created | user_id=%s | reset_id=%s | status=PENDING",
                    user_id,
                    reset_id,
                )
            else:
                logger.info(
                    "Password reset request accepted with no matching active USER account"
                )

            return True

    def list_pending_password_resets(self, limit: int = 50) -> list[dict]:
        """Return pending reset requests for administrator review."""
        with self._operation(
            "List pending password resets",
            requested_limit=limit,
        ):
            session = self._get_session()
            safe_limit = max(1, min(int(limit), 100))

            rows = session.sql(
                f"""
                SELECT
                    R.RESET_ID,
                    R.USER_ID,
                    R.EMAIL,
                    R.STATUS,
                    R.REQUESTED_AT,
                    R.COMPLETED_AT,
                    U.NAME,
                    U.ROLE,
                    U.STATUS AS USER_STATUS
                FROM {self.RESET_TABLE_NAME} R
                INNER JOIN {self.TABLE_NAME} U
                    ON R.USER_ID = U.USER_ID
                WHERE UPPER(R.STATUS) = 'PENDING'
                  AND UPPER(U.ROLE) = 'USER'
                ORDER BY R.REQUESTED_AT DESC
                LIMIT {safe_limit}
                """
            ).collect()

            logger.info(
                "Pending password resets retrieved | count=%s | limit=%s",
                len(rows),
                safe_limit,
            )

            return [
                {
                    "reset_id": row["RESET_ID"],
                    "user_id": row["USER_ID"],
                    "email": row["EMAIL"],
                    "status": row["STATUS"],
                    "requested_at": row["REQUESTED_AT"],
                    "completed_at": row["COMPLETED_AT"],
                    "name": row["NAME"],
                    "role": row["ROLE"],
                    "user_status": row["USER_STATUS"],
                }
                for row in rows
            ]

    def complete_password_reset(
        self,
        reset_id: str,
        new_password: str,
    ) -> bool:
        """Set a new password for a pending USER reset request."""
        with self._operation(
            "Password reset completion",
            reset_id=(reset_id or "").strip() or "<empty>",
        ) as op:
            if not reset_id or not reset_id.strip():
                op["outcome"] = "REJECTED"
                op["reason"] = "missing_reset_request"
                raise ValueError("Reset request is required.")

            if not new_password:
                op["outcome"] = "REJECTED"
                op["reason"] = "missing_new_password"
                raise ValueError("New password is required.")

            if len(new_password) < 8:
                op["outcome"] = "REJECTED"
                op["reason"] = "weak_password"
                raise ValueError("Password must contain at least 8 characters.")

            session = self._get_session()

            rows = session.sql(
                f"""
                SELECT
                    R.RESET_ID,
                    R.USER_ID,
                    U.ROLE,
                    U.STATUS AS USER_STATUS
                FROM {self.RESET_TABLE_NAME} R
                INNER JOIN {self.TABLE_NAME} U
                    ON R.USER_ID = U.USER_ID
                WHERE R.RESET_ID = ?
                  AND UPPER(R.STATUS) = 'PENDING'
                  AND UPPER(U.ROLE) = 'USER'
                LIMIT 1
                """,
                params=[reset_id.strip()],
            ).collect()

            if not rows:
                op["outcome"] = "REJECTED"
                op["reason"] = "reset_not_pending"
                raise ValueError("This password-reset request is no longer pending.")

            user_id = rows[0]["USER_ID"]
            password_hash = self._hash_password(new_password)

            session.sql(
                f"""
                UPDATE {self.TABLE_NAME}
                SET PASSWORD_HASH = ?
                WHERE USER_ID = ?
                  AND UPPER(ROLE) = 'USER'
                """,
                params=[password_hash, user_id],
            ).collect()

            session.sql(
                f"""
                UPDATE {self.RESET_TABLE_NAME}
                SET STATUS = 'COMPLETED',
                    COMPLETED_AT = CURRENT_TIMESTAMP()
                WHERE RESET_ID = ?
                  AND UPPER(STATUS) = 'PENDING'
                """,
                params=[reset_id.strip()],
            ).collect()

            logger.info(
                "Password reset completed | reset_id=%s | user_id=%s | status=COMPLETED",
                reset_id.strip(),
                user_id,
            )
            return True

    def close(self) -> None:
        """Close the session only if AuthService created it."""
        if self._owns_session and self.session is not None:
            try:
                self.session.close()
            finally:
                self.session = None
