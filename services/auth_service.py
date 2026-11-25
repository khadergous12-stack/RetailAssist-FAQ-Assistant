import hashlib
import logging
import secrets
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from providers.snowflake.connection import create_snowflake_session
from services.activity_service import ActivityService
from services.email_service import EmailService


logger = logging.getLogger(__name__)


class AuthService:
    """Authentication and user-account management for SupportAI."""

    TABLE_NAME = "SUPPORT_USERS"
    RESET_TABLE_NAME = "SUPPORT_PASSWORD_RESET_REQUESTS"

    RESET_TOKEN_EXPIRY_MINUTES = 30

    def __init__(self, session=None, email_service=None):
        self.session = session
        self._owns_session = session is None

        # Email service used for password-reset emails.
        self.email_service = email_service or EmailService()

        # Internal status from the most recent login attempt.
        # The UI uses this to distinguish different login failures.
        self.last_login_status = None

    # ============================================================
    # Utility / Logging
    # ============================================================

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

        logger.info(
            "%s started%s",
            operation,
            suffix,
        )

        outcome = "SUCCESS"
        reason = None

        try:
            tracker = {
                "outcome": "SUCCESS",
                "reason": None,
            }

            yield tracker

            outcome = tracker["outcome"]
            reason = tracker["reason"]

        except Exception as exc:
            duration = time.perf_counter() - started

            logger.exception(
                "%s failed | duration=%.3fs | error=%s",
                operation,
                duration,
                type(exc).__name__,
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
        """Return the existing Snowflake session or create one."""
        if self.session is None:
            logger.info("Snowflake session creation started | service=AuthService")

            started = time.perf_counter()

            self.session = create_snowflake_session()

            logger.info(
                "Snowflake session ready | service=AuthService | duration=%.3fs",
                time.perf_counter() - started,
            )

        return self.session

    # ============================================================
    # Email / Password Helpers
    # ============================================================

    @staticmethod
    def _normalize_email(email: str) -> str:
        """Normalize an email address for lookup and storage."""
        return email.strip().lower()

    @staticmethod
    def _hash_reset_token(token: str) -> str:
        """
        Hash a password-reset token before storing it in Snowflake.

        The raw token is never stored in the database.
        """
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_password(
        password: str,
        salt: str | None = None,
    ) -> str:
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
    def _verify_password(
        password: str,
        stored_hash: str,
    ) -> bool:
        """Verify a password against the stored PBKDF2 hash."""
        try:
            salt, iterations, expected_hash = stored_hash.split(
                "$",
                2,
            )

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
    def _validate_signup(
        name: str,
        email: str,
        password: str,
    ) -> None:
        """Validate new-user signup data."""
        if not name or not name.strip():
            raise ValueError("Name is required.")

        if not email or "@" not in email:
            raise ValueError("Please enter a valid email address.")

        if not password:
            raise ValueError("Password is required.")

        if len(password) < 8:
            raise ValueError("Password must contain at least 8 characters.")

    # ============================================================
    # Signup
    # ============================================================

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
                self._validate_signup(
                    name,
                    email,
                    password,
                )

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
                "User account created successfully | "
                "user_id=%s | role=USER | status=ACTIVE",
                user_id,
            )

            return {
                "user_id": user_id,
                "name": name,
                "email": email,
                "role": "USER",
                "status": "ACTIVE",
            }

    # ============================================================
    # Login
    # ============================================================

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

            if not self._verify_password(
                password,
                password_hash,
            ):
                self.last_login_status = "invalid_credentials"

                op["outcome"] = "REJECTED"
                op["reason"] = "invalid_credentials"

                logger.warning(
                    "Login rejected | reason=invalid_credentials | "
                    "user_id=%s | role=%s",
                    user_id,
                    role,
                )

                return None

            # ----------------------------------------------------
            # Password-reset verification guard
            # ----------------------------------------------------
            #
            # ADMIN accounts are not blocked by normal user
            # password-reset verification.
            #
            # USER accounts are blocked when their latest reset
            # has been completed but has not yet been approved.
            # ----------------------------------------------------

            if role == "USER":
                reset_rows = session.sql(
                    f"""
                    SELECT
                        RESET_ID,
                        STATUS,
                        REQUESTED_AT
                    FROM {self.RESET_TABLE_NAME}
                    WHERE USER_ID = ?
                    ORDER BY REQUESTED_AT DESC
                    LIMIT 1
                    """,
                    params=[user_id],
                ).collect()

                if reset_rows:
                    latest_reset = reset_rows[0]

                    reset_status = str(latest_reset["STATUS"]).upper()

                    if reset_status == "COMPLETED":
                        self.last_login_status = "password_reset_pending_verification"

                        op["outcome"] = "REJECTED"
                        op["reason"] = "password_reset_pending_verification"

                        logger.warning(
                            "Login rejected | reason="
                            "password_reset_pending_verification | "
                            "user_id=%s | reset_id=%s",
                            user_id,
                            latest_reset["RESET_ID"],
                        )

                        return None

                    if reset_status == "REJECTED":
                        self.last_login_status = "password_reset_rejected"

                        op["outcome"] = "REJECTED"
                        op["reason"] = "password_reset_rejected"

                        logger.warning(
                            "Login rejected | reason="
                            "password_reset_rejected | "
                            "user_id=%s | reset_id=%s",
                            user_id,
                            latest_reset["RESET_ID"],
                        )

                        return None

            # ----------------------------------------------------
            # Normal successful login
            # ----------------------------------------------------

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
                logger.exception(
                    "Login activity logging failed | user_id=%s",
                    user_id,
                )

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

    # ============================================================
    # Password Reset - Request
    # ============================================================

    def request_password_reset(
        self,
        email: str,
    ) -> bool:
        """
        Create a secure password-reset request and email
        the reset link.

        Security behavior:
        - Generates a cryptographically secure token.
        - Stores only the SHA-256 token hash.
        - Raw token is sent only through the email link.
        - Token expires after 30 minutes.
        - Previous pending reset requests are invalidated.
        - Account existence is not revealed.
        """
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

            # ----------------------------------------------------
            # Find an active normal USER.
            # ----------------------------------------------------

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

            # ----------------------------------------------------
            # Do not reveal whether the account exists.
            # ----------------------------------------------------

            if not rows:
                logger.info(
                    "Password reset request accepted with no "
                    "matching active USER account"
                )

                return True

            user_id = rows[0]["USER_ID"]

            # ----------------------------------------------------
            # Invalidate previous pending requests.
            #
            # This ensures only the newest reset link remains
            # active for this user.
            # ----------------------------------------------------

            session.sql(
                f"""
                UPDATE {self.RESET_TABLE_NAME}
                SET STATUS = 'EXPIRED'
                WHERE USER_ID = ?
                  AND UPPER(STATUS) = 'PENDING'
                """,
                params=[user_id],
            ).collect()

            # ----------------------------------------------------
            # Generate unique reset request ID.
            # ----------------------------------------------------

            reset_id = str(uuid.uuid4())

            # ----------------------------------------------------
            # Generate secure random token.
            # ----------------------------------------------------

            raw_token = secrets.token_urlsafe(32)

            # ----------------------------------------------------
            # Store ONLY the token hash in Snowflake.
            # ----------------------------------------------------

            token_hash = self._hash_reset_token(raw_token)

            # ----------------------------------------------------
            # Set 30-minute expiration.
            #
            # Snowflake column is TIMESTAMP_NTZ, so store a
            # timezone-naive UTC datetime.
            # ----------------------------------------------------

            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
                minutes=self.RESET_TOKEN_EXPIRY_MINUTES
            )

            # ----------------------------------------------------
            # Insert reset request.
            # ----------------------------------------------------

            session.sql(
                f"""
                INSERT INTO {self.RESET_TABLE_NAME}
                (
                    RESET_ID,
                    USER_ID,
                    EMAIL,
                    TOKEN_HASH,
                    STATUS,
                    REQUESTED_AT,
                    EXPIRES_AT
                )
                VALUES (
                    ?,
                    ?,
                    ?,
                    ?,
                    'PENDING',
                    CURRENT_TIMESTAMP(),
                    ?
                )
                """,
                params=[
                    reset_id,
                    user_id,
                    email,
                    token_hash,
                    expires_at,
                ],
            ).collect()

            # ----------------------------------------------------
            # Build reset link.
            # ----------------------------------------------------

            reset_link = self.email_service.build_password_reset_link(
                reset_id=reset_id,
                token=raw_token,
            )

            # ----------------------------------------------------
            # Send email.
            # ----------------------------------------------------

            email_sent = self.email_service.send_password_reset_email(
                recipient_email=email,
                reset_link=reset_link,
            )

            if not email_sent:
                # If email delivery failed, invalidate the
                # reset request so the token cannot be used.
                session.sql(
                    f"""
                    UPDATE {self.RESET_TABLE_NAME}
                    SET STATUS = 'EXPIRED'
                    WHERE RESET_ID = ?
                      AND UPPER(STATUS) = 'PENDING'
                    """,
                    params=[reset_id],
                ).collect()

                op["outcome"] = "REJECTED"
                op["reason"] = "email_delivery_failed"

                raise RuntimeError(
                    "Unable to send the password reset email. Please try again later."
                )

            logger.info(
                "Password reset request created and email sent | "
                "user_id=%s | reset_id=%s | status=PENDING",
                user_id,
                reset_id,
            )

            return True

    # ============================================================
    # Password Reset - Token Validation
    # ============================================================

    def validate_password_reset_token(
        self,
        reset_id: str,
        token: str,
    ) -> bool:
        """
        Validate a password-reset link.

        The token must:
        - exist,
        - match the stored SHA-256 hash,
        - belong to a PENDING reset,
        - not be expired.

        A completed/approved/rejected/expired token cannot
        be reused.
        """
        with self._operation(
            "Password reset token validation",
            reset_id=(reset_id or "").strip() or "<empty>",
        ) as op:
            reset_id = (reset_id or "").strip()
            token = (token or "").strip()

            if not reset_id:
                op["outcome"] = "REJECTED"
                op["reason"] = "missing_reset_id"

                return False

            if not token:
                op["outcome"] = "REJECTED"
                op["reason"] = "missing_token"

                return False

            session = self._get_session()

            rows = session.sql(
                f"""
                SELECT
                    RESET_ID,
                    TOKEN_HASH,
                    STATUS,
                    EXPIRES_AT
                FROM {self.RESET_TABLE_NAME}
                WHERE RESET_ID = ?
                LIMIT 1
                """,
                params=[reset_id],
            ).collect()

            if not rows:
                op["outcome"] = "REJECTED"
                op["reason"] = "reset_not_found"

                return False

            row = rows[0]

            status = str(row["STATUS"]).upper()

            if status != "PENDING":
                op["outcome"] = "REJECTED"
                op["reason"] = "reset_not_pending"

                return False

            expires_at = row["EXPIRES_AT"]

            # Snowflake TIMESTAMP_NTZ is timezone-naive.
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

            if expires_at is None or now_utc >= expires_at:
                session.sql(
                    f"""
                    UPDATE {self.RESET_TABLE_NAME}
                    SET STATUS = 'EXPIRED'
                    WHERE RESET_ID = ?
                      AND UPPER(STATUS) = 'PENDING'
                    """,
                    params=[reset_id],
                ).collect()

                op["outcome"] = "REJECTED"
                op["reason"] = "token_expired"

                return False

            supplied_token_hash = self._hash_reset_token(token)

            stored_token_hash = row["TOKEN_HASH"] or ""

            if not secrets.compare_digest(
                supplied_token_hash,
                stored_token_hash,
            ):
                op["outcome"] = "REJECTED"
                op["reason"] = "invalid_token"

                logger.warning(
                    "Password reset token validation rejected | "
                    "reason=invalid_token | reset_id=%s",
                    reset_id,
                )

                return False

            logger.info(
                "Password reset token validated successfully | reset_id=%s",
                reset_id,
            )

            return True

    # ============================================================
    # Password Reset - Complete
    # ============================================================

    def complete_password_reset(
        self,
        reset_id: str,
        token: str,
        new_password: str,
    ) -> bool:
        """
        Set the user's new password after validating the
        emailed reset token.

        IMPORTANT:
        The password is changed here, but the user cannot log in
        until an administrator approves the completed reset.
        """
        with self._operation(
            "Password reset completion",
            reset_id=(reset_id or "").strip() or "<empty>",
        ) as op:
            reset_id = (reset_id or "").strip()
            token = (token or "").strip()

            if not reset_id:
                op["outcome"] = "REJECTED"
                op["reason"] = "missing_reset_request"

                raise ValueError("Reset request is required.")

            if not token:
                op["outcome"] = "REJECTED"
                op["reason"] = "missing_reset_token"

                raise ValueError("Reset token is required.")

            if not new_password:
                op["outcome"] = "REJECTED"
                op["reason"] = "missing_new_password"

                raise ValueError("New password is required.")

            if len(new_password) < 8:
                op["outcome"] = "REJECTED"
                op["reason"] = "weak_password"

                raise ValueError("Password must contain at least 8 characters.")

            # ----------------------------------------------------
            # Validate token before changing the password.
            # ----------------------------------------------------

            if not self.validate_password_reset_token(
                reset_id,
                token,
            ):
                op["outcome"] = "REJECTED"
                op["reason"] = "invalid_or_expired_token"

                raise ValueError("This password-reset link is invalid or has expired.")

            session = self._get_session()

            # ----------------------------------------------------
            # Retrieve reset request + user.
            # ----------------------------------------------------

            rows = session.sql(
                f"""
                SELECT
                    R.RESET_ID,
                    R.USER_ID,
                    R.STATUS,
                    U.ROLE,
                    U.STATUS AS USER_STATUS
                FROM {self.RESET_TABLE_NAME} R
                INNER JOIN {self.TABLE_NAME} U
                    ON R.USER_ID = U.USER_ID
                WHERE R.RESET_ID = ?
                  AND UPPER(R.STATUS) = 'PENDING'
                  AND UPPER(U.ROLE) = 'USER'
                  AND UPPER(U.STATUS) = 'ACTIVE'
                LIMIT 1
                """,
                params=[reset_id],
            ).collect()

            if not rows:
                op["outcome"] = "REJECTED"
                op["reason"] = "reset_not_pending"

                raise ValueError("This password-reset request is no longer valid.")

            row = rows[0]

            user_id = row["USER_ID"]

            # ----------------------------------------------------
            # Hash the new password using the existing password
            # hashing mechanism.
            # ----------------------------------------------------

            password_hash = self._hash_password(new_password)

            # ----------------------------------------------------
            # Update user's password.
            # ----------------------------------------------------

            session.sql(
                f"""
                UPDATE {self.TABLE_NAME}
                SET PASSWORD_HASH = ?
                WHERE USER_ID = ?
                  AND UPPER(ROLE) = 'USER'
                  AND UPPER(STATUS) = 'ACTIVE'
                """,
                params=[
                    password_hash,
                    user_id,
                ],
            ).collect()

            # ----------------------------------------------------
            # Mark reset as COMPLETED.
            #
            # Admin approval is still required.
            # ----------------------------------------------------

            session.sql(
                f"""
                UPDATE {self.RESET_TABLE_NAME}
                SET STATUS = 'COMPLETED',
                    COMPLETED_AT = CURRENT_TIMESTAMP()
                WHERE RESET_ID = ?
                  AND UPPER(STATUS) = 'PENDING'
                """,
                params=[reset_id],
            ).collect()

            logger.info(
                "Password reset completed | "
                "reset_id=%s | user_id=%s | status=COMPLETED",
                reset_id,
                user_id,
            )

            return True

    # ============================================================
    # Password Reset - Admin Queue
    # ============================================================

    def list_pending_password_resets(
        self,
        limit: int = 50,
    ) -> list[dict]:
        """
        Return password resets that have been completed by the
        user and are waiting for administrator verification.
        """
        with self._operation(
            "List password resets awaiting verification",
            requested_limit=limit,
        ):
            session = self._get_session()

            safe_limit = max(
                1,
                min(int(limit), 100),
            )

            rows = session.sql(
                f"""
                SELECT
                    R.RESET_ID,
                    R.USER_ID,
                    R.EMAIL,
                    R.STATUS,
                    R.REQUESTED_AT,
                    R.EXPIRES_AT,
                    R.COMPLETED_AT,
                    R.VERIFIED_AT,
                    R.VERIFIED_BY,
                    U.NAME,
                    U.ROLE,
                    U.STATUS AS USER_STATUS
                FROM {self.RESET_TABLE_NAME} R
                INNER JOIN {self.TABLE_NAME} U
                    ON R.USER_ID = U.USER_ID
                WHERE UPPER(R.STATUS) = 'COMPLETED'
                  AND UPPER(U.ROLE) = 'USER'
                ORDER BY R.COMPLETED_AT DESC
                LIMIT {safe_limit}
                """
            ).collect()

            logger.info(
                "Password resets awaiting verification retrieved | count=%s | limit=%s",
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
                    "expires_at": row["EXPIRES_AT"],
                    "completed_at": row["COMPLETED_AT"],
                    "verified_at": row["VERIFIED_AT"],
                    "verified_by": row["VERIFIED_BY"],
                    "name": row["NAME"],
                    "role": row["ROLE"],
                    "user_status": row["USER_STATUS"],
                }
                for row in rows
            ]

    # ============================================================
    # Password Reset - Admin Verification Helpers
    # ============================================================

    def _verify_admin(
        self,
        admin_user_id: str,
    ) -> bool:
        """Verify that the requesting account is an active ADMIN."""
        admin_user_id = (admin_user_id or "").strip()

        if not admin_user_id:
            return False

        session = self._get_session()

        rows = session.sql(
            f"""
            SELECT USER_ID
            FROM {self.TABLE_NAME}
            WHERE USER_ID = ?
              AND UPPER(ROLE) = 'ADMIN'
              AND UPPER(STATUS) = 'ACTIVE'
            LIMIT 1
            """,
            params=[admin_user_id],
        ).collect()

        return bool(rows)

    def approve_password_reset(
        self,
        reset_id: str,
        admin_user_id: str,
    ) -> bool:
        """
        Approve a completed password reset.

        The admin does NOT enter or see the password.
        """
        with self._operation(
            "Approve password reset",
            reset_id=(reset_id or "").strip() or "<empty>",
            admin_user_id=(admin_user_id or "").strip() or "<empty>",
        ) as op:
            reset_id = (reset_id or "").strip()
            admin_user_id = (admin_user_id or "").strip()

            if not reset_id:
                op["outcome"] = "REJECTED"
                op["reason"] = "missing_reset_id"

                raise ValueError("Reset request is required.")

            if not self._verify_admin(admin_user_id):
                op["outcome"] = "REJECTED"
                op["reason"] = "unauthorized_admin"

                raise PermissionError("Administrator verification is required.")

            session = self._get_session()

            rows = session.sql(
                f"""
                SELECT
                    RESET_ID,
                    STATUS
                FROM {self.RESET_TABLE_NAME}
                WHERE RESET_ID = ?
                LIMIT 1
                """,
                params=[reset_id],
            ).collect()

            if not rows:
                op["outcome"] = "REJECTED"
                op["reason"] = "reset_not_found"

                raise ValueError("Password-reset request not found.")

            current_status = str(rows[0]["STATUS"]).upper()

            if current_status != "COMPLETED":
                op["outcome"] = "REJECTED"
                op["reason"] = "reset_not_completed"

                raise ValueError("Only completed password resets can be approved.")

            session.sql(
                f"""
                UPDATE {self.RESET_TABLE_NAME}
                SET STATUS = 'APPROVED',
                    VERIFIED_AT = CURRENT_TIMESTAMP(),
                    VERIFIED_BY = ?
                WHERE RESET_ID = ?
                  AND UPPER(STATUS) = 'COMPLETED'
                """,
                params=[
                    admin_user_id,
                    reset_id,
                ],
            ).collect()

            logger.info(
                "Password reset approved | "
                "reset_id=%s | verified_by=%s | status=APPROVED",
                reset_id,
                admin_user_id,
            )

            return True

    def reject_password_reset(
        self,
        reset_id: str,
        admin_user_id: str,
    ) -> bool:
        """
        Reject a completed password reset.

        The admin does NOT enter or see the password.
        """
        with self._operation(
            "Reject password reset",
            reset_id=(reset_id or "").strip() or "<empty>",
            admin_user_id=(admin_user_id or "").strip() or "<empty>",
        ) as op:
            reset_id = (reset_id or "").strip()
            admin_user_id = (admin_user_id or "").strip()

            if not reset_id:
                op["outcome"] = "REJECTED"
                op["reason"] = "missing_reset_id"

                raise ValueError("Reset request is required.")

            if not self._verify_admin(admin_user_id):
                op["outcome"] = "REJECTED"
                op["reason"] = "unauthorized_admin"

                raise PermissionError("Administrator verification is required.")

            session = self._get_session()

            rows = session.sql(
                f"""
                SELECT
                    RESET_ID,
                    STATUS
                FROM {self.RESET_TABLE_NAME}
                WHERE RESET_ID = ?
                LIMIT 1
                """,
                params=[reset_id],
            ).collect()

            if not rows:
                op["outcome"] = "REJECTED"
                op["reason"] = "reset_not_found"

                raise ValueError("Password-reset request not found.")

            current_status = str(rows[0]["STATUS"]).upper()

            if current_status != "COMPLETED":
                op["outcome"] = "REJECTED"
                op["reason"] = "reset_not_completed"

                raise ValueError("Only completed password resets can be rejected.")

            session.sql(
                f"""
                UPDATE {self.RESET_TABLE_NAME}
                SET STATUS = 'REJECTED',
                    VERIFIED_AT = CURRENT_TIMESTAMP(),
                    VERIFIED_BY = ?
                WHERE RESET_ID = ?
                  AND UPPER(STATUS) = 'COMPLETED'
                """,
                params=[
                    admin_user_id,
                    reset_id,
                ],
            ).collect()

            logger.info(
                "Password reset rejected | "
                "reset_id=%s | verified_by=%s | status=REJECTED",
                reset_id,
                admin_user_id,
            )

            return True

    # ============================================================
    # Session Cleanup
    # ============================================================

    def close(self) -> None:
        """Close the session only if AuthService created it."""
        if self._owns_session and self.session is not None:
            try:
                self.session.close()
            finally:
                self.session = None
