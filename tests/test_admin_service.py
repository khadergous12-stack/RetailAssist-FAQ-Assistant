from services.admin_service import AdminService


def main():
    admin = AdminService()

    try:
        print("\n=== USER LIST ===")

        users = admin.list_users()

        for user in users:
            print(
                user["name"],
                "|",
                user["email"],
                "|",
                user["role"],
                "|",
                user["status"],
            )

        print("\n=== USER STATS ===")

        stats = admin.get_user_stats()
        print(stats)

    finally:
        admin.close()


if __name__ == "__main__":
    main()
