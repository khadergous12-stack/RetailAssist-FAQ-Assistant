import os

from app.controller import RetailAssistController
from rag.service import RAGService


def create_controller() -> RetailAssistController:
    """
    Create RetailAssist using Snowflake Cortex.
    """

    mode = os.environ.get(
        "RETAIL_ASSIST_MODE",
        "SNOWFLAKE",
    ).upper()

    document_store = None

    if mode == "SNOWFLAKE":
        from providers.snowflake.connection import create_snowflake_session
        from providers.snowflake.generator import SnowflakeGenerator
        from providers.snowflake.retriever import SnowflakeRetriever
        from providers.snowflake.document_store import DocumentStore

        session = create_snowflake_session()

        retriever = SnowflakeRetriever(
            session=session,
        )

        generator = SnowflakeGenerator(
            session=session,
        )

        document_store = DocumentStore(
            session=session,
        )

    elif mode == "DEMO":
        from app.demo_providers import DemoGenerator, DemoRetriever

        retriever = DemoRetriever()
        generator = DemoGenerator()

    else:
        raise ValueError("Invalid RETAIL_ASSIST_MODE. Use 'DEMO' or 'SNOWFLAKE'.")

    rag_service = RAGService(
        retriever=retriever,
        generator=generator,
    )

    return RetailAssistController(
        rag_service=rag_service,
        document_store=document_store,
    )


# ============================================================
# Streamlit application entry point
# ============================================================

from app.ui import run_app


if __name__ == "__main__":
    controller = create_controller()
    run_app(controller)
