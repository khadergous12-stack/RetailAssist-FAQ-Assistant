from __future__ import annotations

import logging
import time

from config.settings import Settings, load_settings

from openai import OpenAI
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from rag.contracts import Generator


logger = logging.getLogger(__name__)


class OpenAIGenerator:
    """
    OpenRouter-backed implementation of the provider-neutral
    Generator contract.

    OpenRouter exposes an OpenAI-compatible API.

    Logging in this class is diagnostic only. It records provider activity,
    timing, and failures without logging API keys or the full prompt.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ):
        settings = settings or load_settings()

        self.api_key = api_key or settings.openrouter_api_key
        self.model = model or settings.openrouter_model
        self.max_tokens = settings.openrouter_max_tokens

        if not self.api_key:
            logger.error("OpenRouter initialization failed: API key is not configured.")
            raise ValueError(
                "OpenRouter API key is not configured. Set OPENROUTER_API_KEY."
            )

        if not self.model:
            logger.error("OpenRouter initialization failed: model is not configured.")
            raise ValueError(
                "OpenRouter model is not configured. Set OPENROUTER_MODEL."
            )

        logger.info(
            "Initializing OpenRouter generator | model=%s | max_tokens=%s | timeout=%ss",
            self.model,
            self.max_tokens,
            settings.openrouter_timeout,
        )

        try:
            self.client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=self.api_key,
                timeout=settings.openrouter_timeout,
            )

            logger.info(
                "OpenRouter generator initialized successfully | model=%s",
                self.model,
            )

        except Exception:
            logger.exception(
                "Failed to initialize OpenRouter client | model=%s",
                self.model,
            )
            raise

    def generate(self, prompt: str) -> str:
        if not prompt or not prompt.strip():
            logger.warning("OpenRouter generation skipped because prompt was empty.")
            raise ValueError("Prompt cannot be empty.")

        start_time = time.perf_counter()

        logger.info(
            "OpenRouter generation started | model=%s | max_tokens=%s | prompt_length=%s",
            self.model,
            self.max_tokens,
            len(prompt),
        )

        # A successful HTTP response can occasionally contain no usable
        # assistant content. Retry once before surfacing the failure to the UI.
        # This is intentionally limited to one retry to avoid long delays.
        max_attempts = 2

        for attempt in range(1, max_attempts + 1):
            attempt_start = time.perf_counter()

            logger.info(
                "OpenRouter API attempt started | attempt=%s/%s",
                attempt,
                max_attempts,
            )

            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt,
                        }
                    ],
                    max_tokens=self.max_tokens,
                )

            except AuthenticationError as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.error(
                    "OpenRouter authentication failed | attempt=%s/%s | duration=%.3fs",
                    attempt,
                    max_attempts,
                    elapsed,
                )

                raise RuntimeError(
                    "OpenRouter authentication failed. Check OPENROUTER_API_KEY."
                ) from exc

            except RateLimitError as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.warning(
                    "OpenRouter rate limit reached | attempt=%s/%s | duration=%.3fs",
                    attempt,
                    max_attempts,
                    elapsed,
                )

                raise RuntimeError(
                    "OpenRouter rate limit reached. Please try again later."
                ) from exc

            except APITimeoutError as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.error(
                    "OpenRouter request timed out | attempt=%s/%s | duration=%.3fs",
                    attempt,
                    max_attempts,
                    elapsed,
                )

                raise RuntimeError(
                    "OpenRouter request timed out. Please try again."
                ) from exc

            except APIConnectionError as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.error(
                    "Unable to connect to OpenRouter | attempt=%s/%s | duration=%.3fs",
                    attempt,
                    max_attempts,
                    elapsed,
                )

                raise RuntimeError(
                    "Unable to connect to OpenRouter. "
                    "Check your network connection and try again."
                ) from exc

            except BadRequestError as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.error(
                    "OpenRouter rejected the request | attempt=%s/%s | duration=%.3fs | model=%s",
                    attempt,
                    max_attempts,
                    elapsed,
                    self.model,
                )

                raise RuntimeError(
                    "OpenRouter rejected the request. "
                    "Check the selected model and request configuration."
                ) from exc

            except APIStatusError as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.error(
                    "OpenRouter API request failed | http_status=%s | attempt=%s/%s | duration=%.3fs | model=%s",
                    exc.status_code,
                    attempt,
                    max_attempts,
                    elapsed,
                    self.model,
                )

                raise RuntimeError(
                    f"OpenRouter request failed (HTTP {exc.status_code}). Please try again."
                ) from exc

            except Exception as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.exception(
                    "Unexpected OpenRouter generation failure | attempt=%s/%s | duration=%.3fs | model=%s",
                    attempt,
                    max_attempts,
                    elapsed,
                    self.model,
                )

                raise RuntimeError(
                    "OpenRouter generation failed. Please try again."
                ) from exc

            attempt_elapsed = time.perf_counter() - attempt_start

            if not response.choices:
                logger.warning(
                    "OpenRouter returned no choices | attempt=%s/%s | duration=%.3fs | response_model=%s",
                    attempt,
                    max_attempts,
                    attempt_elapsed,
                    getattr(response, "model", None),
                )

                if attempt < max_attempts:
                    logger.info(
                        "Retrying OpenRouter request because no choices were returned."
                    )
                    continue

                logger.error(
                    "OpenRouter returned no choices after retrying | total_duration=%.3fs",
                    time.perf_counter() - start_time,
                )
                raise RuntimeError("OpenRouter returned no choices.")

            choice = response.choices[0]
            message = getattr(choice, "message", None)
            answer = getattr(message, "content", None) if message is not None else None

            if answer is not None and str(answer).strip():
                total_elapsed = time.perf_counter() - start_time

                if total_elapsed > 10:
                    logger.warning(
                        "Slow OpenRouter generation detected | duration=%.3fs | model=%s | attempts=%s",
                        total_elapsed,
                        self.model,
                        attempt,
                    )
                else:
                    logger.info(
                        "OpenRouter generation completed successfully | duration=%.3fs | response_length=%s | attempts=%s",
                        total_elapsed,
                        len(str(answer)),
                        attempt,
                    )

                return str(answer).strip()

            finish_reason = getattr(choice, "finish_reason", None)

            logger.warning(
                "OpenRouter returned an unusable empty content response | "
                "attempt=%s/%s | attempt_duration=%.3fs | finish_reason=%s | response_model=%s",
                attempt,
                max_attempts,
                attempt_elapsed,
                finish_reason,
                getattr(response, "model", None),
            )

            if attempt < max_attempts:
                logger.info(
                    "Retrying OpenRouter request because assistant content was empty."
                )
                continue

            total_elapsed = time.perf_counter() - start_time

            logger.error(
                "OpenRouter returned an empty response after retrying | total_duration=%.3fs | attempts=%s",
                total_elapsed,
                attempt,
            )

            raise RuntimeError("OpenRouter returned an empty response.")

        # Defensive fallback; the loop should always return or raise.
        logger.error("OpenRouter generation ended without a response.")
        raise RuntimeError("OpenRouter generation failed. Please try again.")
