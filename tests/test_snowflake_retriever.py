from providers.snowflake.connection import create_snowflake_session
from providers.snowflake.retriever import SnowflakeRetriever


def main():
    print("Connecting to Snowflake...")

    session = create_snowflake_session()

    print("Snowflake connection successful!")

    retriever = SnowflakeRetriever(session=session)

    print("SnowflakeRetriever created successfully!")

    question = "How long does standard delivery take?"

    print(f"\nQuery: {question}")

    results = retriever.retrieve(
        query=question,
        top_k=5,
    )

    print(f"\nRetrieved chunks: {len(results)}")

    for index, chunk in enumerate(results, start=1):
        print("\n" + "=" * 60)
        print(f"Result {index}")
        print("=" * 60)
        print(f"Chunk ID:      {chunk.chunk_id}")
        print(f"Document:      {chunk.document_name}")
        print(f"Category:      {chunk.category}")
        print(f"Chunk Index:   {chunk.chunk_index}")
        print(f"Score:         {chunk.score}")
        print(f"Text:\n{chunk.chunk_text}")


if __name__ == "__main__":
    main()
