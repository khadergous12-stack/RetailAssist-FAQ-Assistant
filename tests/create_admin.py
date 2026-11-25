from getpass import getpass

from services.auth_service import AuthService


def main():
    print("=== SupportAI Admin Account Setup ===")

    name = input("Admin name: ").strip()
    email = input("Admin email: ").strip()
    password = getpass("Admin password: ")
    confirm_password = getpass("Confirm password: ")

    if password != confirm_password:
        print("ERROR: Passwords do not match.")
        return

    auth = AuthService()

    try:
        # Validate using the same password rules as normal signup.
        auth._validate_signup(name, email, password)

        normalized_email = auth._normalize_email(email)
        session = auth._get_session()

        existing = session.sql(
            f"""
            SELECT USER_ID
            FROM {auth.TABLE_NAME}
            WHERE LOWER(EMAIL) = ?
            LIMIT 1
            """,
            params=[normalized_email],
        ).collect()

        if existing:
            print("ERROR: An account with this email already exists.")
            return

        import uuid

        user_id = str(uuid.uuid4())
        password_hash = auth._hash_password(password)

        session.sql(
            f"""
            INSERT INTO {auth.TABLE_NAME}
            (
                USER_ID,
                NAME,
                EMAIL,
                PASSWORD_HASH,
                ROLE,
                STATUS
            )
            VALUES (?, ?, ?, ?, 'ADMIN', 'ACTIVE')
            """,
            params=[
                user_id,
                name,
                normalized_email,
                password_hash,
            ],
        ).collect()

        print("\nADMIN ACCOUNT CREATED SUCCESSFULLY")
        print(f"User ID : {user_id}")
        print(f"Name    : {name}")
        print(f"Email   : {normalized_email}")
        print("Role    : ADMIN")
        print("Status  : ACTIVE")

    except Exception as exc:
        print(f"\nERROR: {exc}")

    finally:
        auth.close()


if __name__ == "__main__":
    main()
