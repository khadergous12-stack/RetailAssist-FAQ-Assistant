from __future__ import annotations

import logging
import time

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
    start = time.perf_counter()
    logger.info("Application controller initialization started.")

    try:
        settings = load_settings()
        logger.info(
            "Application settings loaded | mode=%s | provider=%s",
            settings.retail_assist_mode,
            settings.default_ai_provider,
        )

        mode = settings.retail_assist_mode
        document_store = None
        session = None

        if mode == "SNOWFLAKE":
            logger.info("Snowflake application mode initialization started.")

            from providers.factory import create_generator
            from providers.snowflake.connection import create_snowflake_session
            from providers.snowflake.retriever import SnowflakeRetriever
            from providers.snowflake.document_store import DocumentStore

            connection_start = time.perf_counter()
            logger.info("Snowflake connection started.")

            session = create_snowflake_session()

            logger.info(
                "Snowflake connection established | duration=%.3fs",
                time.perf_counter() - connection_start,
            )

            retriever_start = time.perf_counter()
            logger.info("Snowflake retriever initialization started.")

            retriever = SnowflakeRetriever(
                session=session,
            )

            logger.info(
                "Snowflake retriever initialized | duration=%.3fs",
                time.perf_counter() - retriever_start,
            )

            generator_start = time.perf_counter()
            logger.info(
                "AI generator initialization started | provider=%s",
                settings.default_ai_provider,
            )

            generator = create_generator(
                provider_name=settings.default_ai_provider,
                session=session,
                settings=settings,
            )

            logger.info(
                "AI generator initialized | provider=%s | duration=%.3fs",
                settings.default_ai_provider,
                time.perf_counter() - generator_start,
            )

            document_store_start = time.perf_counter()
            logger.info("Document store initialization started.")

            document_store = DocumentStore(
                session=session,
            )

            logger.info(
                "Document store initialized | duration=%.3fs",
                time.perf_counter() - document_store_start,
            )

        elif mode == "DEMO":
            logger.info("Demo application mode initialization started.")

            from app.demo_providers import DemoGenerator, DemoRetriever

            retriever = DemoRetriever()
            generator = DemoGenerator()

            logger.info("Demo providers initialized successfully.")

        else:
            logger.error(
                "Application initialization rejected | reason=invalid_mode | mode=%s | duration=%.3fs",
                mode,
                time.perf_counter() - start,
            )
            raise ValueError("Invalid RETAIL_ASSIST_MODE. Use 'DEMO' or 'SNOWFLAKE'.")

        rag_start = time.perf_counter()
        logger.info("RAG service initialization started.")

        rag_service = RAGService(
            retriever=retriever,
            generator=generator,
        )

        logger.info(
            "RAG service initialized | duration=%.3fs",
            time.perf_counter() - rag_start,
        )

        controller_start = time.perf_counter()
        controller = RetailAssistController(
            rag_service=rag_service,
            document_store=document_store,
            session=session,
        )

        logger.info(
            "Application controller initialized | duration=%.3fs | total_duration=%.3fs",
            time.perf_counter() - controller_start,
            time.perf_counter() - start,
        )

        total_duration = time.perf_counter() - start

        if total_duration >= 10:
            logger.warning(
                "Slow application controller initialization | mode=%s | duration=%.3fs",
                mode,
                total_duration,
            )

        return controller

    except Exception as exc:
        logger.exception(
            "Application controller initialization failed | exception=%s | duration=%.3fs",
            type(exc).__name__,
            time.perf_counter() - start,
        )
        raise


# ============================================================
# Streamlit application entry point
# ============================================================

from app.ui import run_app


if __name__ == "__main__":
    startup_start = time.perf_counter()
    logger.info("Support AI application startup started.")

    try:
        # Streamlit reruns this script whenever widgets change. Keep the
        # controller alive for the current browser session so we do not
        # reconnect to Snowflake and rebuild providers on every rerun.
        import streamlit as st

        if "supportai_controller" not in st.session_state:
            logger.info("Creating Support AI controller for new Streamlit session.")
            st.session_state.supportai_controller = create_controller()
        else:
            logger.info(
                "Reusing existing Support AI controller from Streamlit session state."
            )

        controller = st.session_state.supportai_controller

        logger.info("Starting Streamlit UI.")
        run_app(controller)

        logger.info(
            "Support AI application run completed | duration=%.3fs",
            time.perf_counter() - startup_start,
        )

    except Exception as exc:
        logger.exception(
            "Application startup failed | exception=%s | duration=%.3fs",
            type(exc).__name__,
            time.perf_counter() - startup_start,
        )
        raise
