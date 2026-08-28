from providers.snowflake.connection import create_snowflake_session
from providers.snowflake.generator import SnowflakeGenerator


def main():
    print("Connecting to Snowflake...")

    session = create_snowflake_session()

    print("Snowflake connection successful!")

    generator = SnowflakeGenerator(session=session)

    print("SnowflakeGenerator created successfully!")

    prompt = """
You are a helpful retail FAQ assistant.

Answer the following question briefly and clearly:

How long does standard delivery take?
"""

    print("\nGenerating answer...")

    answer = generator.generate(prompt)

    print("\nGenerated Answer:")
    print(answer)

    session.close()


if __name__ == "__main__":
    main()
