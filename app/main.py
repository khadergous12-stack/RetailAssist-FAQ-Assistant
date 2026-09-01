from __future__ import annotations

import logging

from app.controller import RetailAssistController
from rag.service import RAGService
from config.settings import load_settings
from config.logging_config import setup_logging


logger = logging.getLogger(__name__)


# Configure logging once for the Streamlit process.
try:
    _startup_settings = load_settings()
    setup_logging()
except Exception:
    # Preserve normal startup failure behavior while making configuration
    # errors visible to the default Python stderr logger.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.exception("Failed to initialize centralized logging.")
    raise


def create_controller() -> RetailAssistController:
    """
    Create the Support AI application.

    Snowflake Cortex Search remains the retrieval layer.
    The generation provider is selected through centralized settings
    and created by the provider factory.
    """
    logger.info("Starting application controller initialization.")

    try:
        settings = load_settings()

        logger.info(
            "Application settings loaded. Mode=%s, Provider=%s",
            settings.retail_assist_mode,
            settings.default_ai_provider,
        )

        mode = settings.retail_assist_mode

        document_store = None
        session = None

        if mode == "SNOWFLAKE":
            logger.info("Initializing Snowflake mode.")

            from providers.factory import create_generator
            from providers.snowflake.connection import create_snowflake_session
            from providers.snowflake.retriever import SnowflakeRetriever
            from providers.snowflake.document_store import DocumentStore

            logger.info("Connecting to Snowflake.")
            session = create_snowflake_session()
            logger.info("Snowflake connection established successfully.")

            logger.info("Initializing Snowflake retriever.")
            retriever = SnowflakeRetriever(
                session=session,
            )
            logger.info("Snowflake retriever initialized.")

            logger.info(
                "Initializing AI generator. Provider=%s",
                settings.default_ai_provider,
            )

            generator = create_generator(
                provider_name=settings.default_ai_provider,
                session=session,
                settings=settings,
            )

            logger.info("AI generator initialized successfully.")

            logger.info("Initializing document store.")
            document_store = DocumentStore(
                session=session,
            )
            logger.info("Document store initialized successfully.")

        elif mode == "DEMO":
            logger.info("Initializing DEMO mode.")

            from app.demo_providers import DemoGenerator, DemoRetriever

            retriever = DemoRetriever()
            generator = DemoGenerator()

            logger.info("Demo providers initialized successfully.")

        else:
            logger.error(
                "Invalid RETAIL_ASSIST_MODE received: %s",
                mode,
            )
            raise ValueError("Invalid RETAIL_ASSIST_MODE. Use 'DEMO' or 'SNOWFLAKE'.")

        logger.info("Initializing RAG service.")

        rag_service = RAGService(
            retriever=retriever,
            generator=generator,
        )

        logger.info("RAG service initialized successfully.")

        controller = RetailAssistController(
            rag_service=rag_service,
            document_store=document_store,
            session=session,
        )

        logger.info("Application controller initialized successfully.")

        return controller

    except Exception:
        logger.exception("Failed to initialize application controller.")
        raise


# ============================================================
# Streamlit application entry point
# ============================================================

from app.ui import run_app


if __name__ == "__main__":
    logger.info("Starting Support AI application.")

    try:
        # Streamlit reruns this script whenever widgets change. Keep the
        # controller alive for the current browser session so we do not
        # reconnect to Snowflake and rebuild providers on every rerun.
        import streamlit as st

        if "supportai_controller" not in st.session_state:
            logger.info("Creating Support AI controller for new Streamlit session.")
            st.session_state.supportai_controller = create_controller()
        else:
            logger.info("Reusing existing Support AI controller from Streamlit session state.")

        controller = st.session_state.supportai_controller

        logger.info("Starting Streamlit UI.")
        run_app(controller)

    except Exception:
        logger.exception("Application startup failed.")
        raise
