from __future__ import annotations

import logging

from app.controller import RetailAssistController
from rag.service import RAGService
from config.settings import load_settings


logging.basicConfig(
    level=load_settings().log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_controller() -> RetailAssistController:
    """
    Create the RetailAssist application.

    Snowflake Cortex Search remains the retrieval layer.
    The initial generation provider is selected through centralized settings
    and created by the provider factory.
    """

    settings = load_settings()

    mode = settings.retail_assist_mode

    document_store = None
    session = None

    if mode == "SNOWFLAKE":
        from providers.factory import create_generator
        from providers.snowflake.connection import create_snowflake_session
        from providers.snowflake.retriever import SnowflakeRetriever
        from providers.snowflake.document_store import DocumentStore

        session = create_snowflake_session()

        retriever = SnowflakeRetriever(
            session=session,
        )

        generator = create_generator(
            provider_name=settings.default_ai_provider,
            session=session,
            settings=settings,
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
        session=session,
    )


# ============================================================
# Streamlit application entry point
# ============================================================

from app.ui import run_app


if __name__ == "__main__":
    controller = create_controller()
    run_app(controller)
