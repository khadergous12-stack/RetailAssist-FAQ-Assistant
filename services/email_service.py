import logging
import os
import smtplib
from email.message import EmailMessage
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


class EmailService:
    """Service responsible for sending SupportAI emails."""

    def __init__(self):
        self.app_base_url = (
            os.getenv("APP_BASE_URL", "http://localhost:8501")
            .strip()
            .rstrip("/")
        )

        self.smtp_host = os.getenv("SMTP_HOST", "").strip()
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_username = os.getenv("SMTP_USERNAME", "").strip()
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.from_email = (
            os.getenv("SMTP_FROM_EMAIL", "").strip()
            or self.smtp_username
        )
        self.use_tls = (
            os.getenv("SMTP_USE_TLS", "true").strip().lower() == "true"
        )

    def is_configured(self) -> bool:
        """Return True when the required SMTP settings are available."""
        return bool(
            self.smtp_host
            and self.smtp_username
            and self.smtp_password
            and self.from_email
        )

    def build_password_reset_link(
        self,
        reset_id: str,
        token: str,
    ) -> str:
        """Build the secure password reset URL."""
        query = urlencode({
            "reset_id": reset_id,
            "token": token,
        })

        return f"{self.app_base_url}/?{query}"

    def send_password_reset_email(
        self,
        recipient_email: str,
        reset_link: str,
    ) -> bool:
        """
        Send a password reset email.

        The reset token is included only inside the reset link.
        No password is ever sent by email.
        """
        if not self.is_configured():
            logger.error(
                "Password reset email service is not configured."
            )
            return False

        recipient_email = recipient_email.strip()

        if not recipient_email:
            logger.warning(
                "Password reset email rejected: empty recipient."
            )
            return False

        message = EmailMessage()

        message["Subject"] = "SupportAI - Reset Your Password"
        message["From"] = self.from_email
        message["To"] = recipient_email

        message.set_content(
            f"""Hello,

We received a request to reset your SupportAI password.

To create a new password, open the secure link below:

{reset_link}

This link is temporary and can only be used once.

If you did not request a password reset, you can safely ignore this email.

SupportAI
"""
        )

        try:
            with smtplib.SMTP(
                self.smtp_host,
                self.smtp_port,
                timeout=20,
            ) as server:

                if self.use_tls:
                    server.starttls()

                server.login(
                    self.smtp_username,
                    self.smtp_password,
                )

                server.send_message(message)

            logger.info(
                "Password reset email sent successfully."
            )
            return True

        except Exception:
            logger.exception(
                "Failed to send password reset email."
            )
            return False