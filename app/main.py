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

    if mode == "SNOWFLAKE":
        from providers.snowflake.connection import create_snowflake_session
        from providers.snowflake.generator import SnowflakeGenerator
        from providers.snowflake.retriever import SnowflakeRetriever

        session = create_snowflake_session()

        retriever = SnowflakeRetriever(
            session=session,
        )

        generator = SnowflakeGenerator(
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
    )


# ============================================================
# Streamlit application entry point
# ============================================================

from app.ui import run_app


if __name__ == "__main__":
    controller = create_controller()
    run_app(controller)
