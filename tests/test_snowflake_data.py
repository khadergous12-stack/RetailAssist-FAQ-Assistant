from providers.snowflake.connection import create_snowflake_session


def main():
    session = create_snowflake_session()

    print("Snowflake connection successful!\n")

    print("Checking POLICY_SOURCES...")

    sources = session.sql(
        """
        SELECT
            DOCUMENT_ID,
            DOCUMENT_NAME,
            CATEGORY
        FROM POLICY_SOURCES
        ORDER BY DOCUMENT_ID
        """
    ).collect()

    print(f"Documents found: {len(sources)}")

    for row in sources:
        print(
            f"{row['DOCUMENT_ID']} | "
            f"{row['DOCUMENT_NAME']} | "
            f"{row['CATEGORY']}"
        )

    print("\nChecking POLICY_CHUNKS...")

    chunks = session.sql(
        """
        SELECT
            COUNT(*) AS TOTAL_CHUNKS
        FROM POLICY_CHUNKS
        """
    ).collect()

    print(f"Total chunks: {chunks[0]['TOTAL_CHUNKS']}")

    session.close()


if __name__ == "__main__":
    main()