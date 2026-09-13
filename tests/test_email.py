from config.settings import load_settings
from services.email_service import EmailService

load_settings()

email_service = EmailService()

print("SMTP configured:", email_service.is_configured())

test_email = input("Enter the email where you want to receive the test email: ").strip()

success = email_service.send_password_reset_email(
    recipient_email=test_email,
    reset_link="http://localhost:8501/?test_reset=true",
)

if success:
    print("✅ Test email sent successfully!")
else:
    print("❌ Failed to send test email.")
