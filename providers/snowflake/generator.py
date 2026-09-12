import logging
import os
import time

from snowflake.cortex import CompleteOptions, complete

from providers.snowflake.connection import create_snowflake_session

logger = logging.getLogger("supportai.snowflake_generator")


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
            raise ValueError(
                "SNOWFLAKE_CORTEX_MODEL must be configured "
                "with a verified Cortex model."
            )

    def generate(self, prompt: str) -> str:
        """
        Generate an answer using Snowflake Cortex.
        """

        if not prompt or not prompt.strip():
            logger.warning("Generation rejected | reason=empty_prompt")
            raise ValueError("Prompt cannot be empty.")

        started = time.perf_counter()
        prompt_length = len(prompt)
        logger.info(
            "Snowflake Cortex generation started | model=%s | prompt_length=%s",
            self.model,
            prompt_length,
        )

        options = CompleteOptions(
            {
                "temperature": 0,
                "max_tokens": 250,
            }
        )

        response = complete(
            self.model,
            prompt,
            options=options,
            session=self.session,
        )

        if response is None:
            raise RuntimeError("Snowflake Cortex returned an empty response.")

        answer = str(response).strip()

        if not answer:
            raise RuntimeError("Snowflake Cortex returned an empty answer.")

        return answer
