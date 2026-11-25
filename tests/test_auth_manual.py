from services.auth_service import AuthService


auth = AuthService()

try:
    user = auth.signup(
        name="Test User",
        email="testuser@supportai.local",
        password="TestPassword123!",
    )

    print("SIGNUP SUCCESS:")
    print(user)

    logged_in = auth.login(
        email="testuser@supportai.local",
        password="TestPassword123!",
    )

    print("\nLOGIN SUCCESS:")
    print(logged_in)

    wrong_password = auth.login(
        email="testuser@supportai.local",
        password="WrongPassword123!",
    )

    print("\nWRONG PASSWORD RESULT:")
    print(wrong_password)

finally:
    auth.close()
