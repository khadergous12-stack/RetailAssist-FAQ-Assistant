from providers.snowflake.connection import create_snowflake_session


def main():
    session = create_snowflake_session()

    result = session.sql(
        "SELECT CURRENT_USER(), CURRENT_DATABASE(), CURRENT_SCHEMA()"
    ).collect()

    print("Snowflake connection successful!")
    print(result)

    session.close()


if __name__ == "__main__":
    main()
