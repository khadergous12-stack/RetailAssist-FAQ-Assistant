import logging
import os
import time

from snowflake.cortex import CompleteOptions, complete

from providers.snowflake.connection import create_snowflake_session


logger = logging.getLogger(__name__)


class SnowflakeGenerator:
    """
    Snowflake implementation of the provider-neutral Generator contract.

    Uses Snowflake Cortex to generate an answer from a grounded prompt.
    """

    def __init__(
        self,
        session=None,
        model: str | None = None,
    ):
        self.session = session or create_snowflake_session()

        self.model = model or os.environ.get("SNOWFLAKE_CORTEX_MODEL")

        if not self.model:
            logger.error(
                "Snowflake Cortex generator initialization failed: model is not configured."
            )
            raise ValueError(
                "SNOWFLAKE_CORTEX_MODEL must be configured "
                "with a verified Cortex model."
            )

        logger.info(
            "Snowflake Cortex generator initialized | model=%s",
            self.model,
        )

    def generate(self, prompt: str) -> str:
        """
        Generate an answer using Snowflake Cortex.
        """

        if not prompt or not prompt.strip():
            logger.warning(
                "Snowflake Cortex generation skipped because prompt was empty."
            )
            raise ValueError("Prompt cannot be empty.")

        start_time = time.perf_counter()

        logger.info(
            "Snowflake Cortex generation started | model=%s | prompt_length=%s",
            self.model,
            len(prompt),
        )

        options = CompleteOptions(
            {
                "temperature": 0,
                "max_tokens": 250,
            }
        )

        try:
            response = complete(
                self.model,
                prompt,
                options=options,
                session=self.session,
            )
        except Exception:
            logger.exception(
                "Snowflake Cortex generation failed | duration=%.3fs",
                time.perf_counter() - start_time,
            )
            raise

        elapsed = time.perf_counter() - start_time

        if response is None:
            logger.error(
                "Snowflake Cortex returned an empty response | duration=%.3fs",
                elapsed,
            )
            raise RuntimeError("Snowflake Cortex returned an empty response.")

        answer = str(response).strip()

        if not answer:
            logger.error(
                "Snowflake Cortex returned an empty answer | duration=%.3fs",
                elapsed,
            )
            raise RuntimeError("Snowflake Cortex returned an empty answer.")

        if elapsed >= 10.0:
            logger.warning(
                "Slow Snowflake Cortex generation detected | duration=%.3fs | model=%s",
                elapsed,
                self.model,
            )
        else:
            logger.info(
                "Snowflake Cortex generation completed | duration=%.3fs | response_length=%s",
                elapsed,
                len(answer),
            )

        return answer
