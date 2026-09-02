from __future__ import annotations

import logging
import os
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
    Groq-backed implementation of the provider-neutral
    Generator contract.

    Groq exposes an OpenAI-compatible API.

    Logging in this class is diagnostic only. It records provider activity,
    timing, and failures without logging API keys or the full prompt.

    Uses Groq through the OpenAI-compatible Chat Completions API.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ):
        settings = settings or load_settings()

        self.api_key = (
            api_key
            or os.getenv("GROQ_API_KEY")
            or getattr(settings, "groq_api_key", None)
        )
        self.model = model or os.getenv("GROQ_MODEL") or "openai/gpt-oss-20b"
        self.max_tokens = int(
            os.getenv("GROQ_MAX_TOKENS") or getattr(settings, "groq_max_tokens", 256)
        )
        self.timeout = int(
            os.getenv("GROQ_TIMEOUT") or getattr(settings, "groq_timeout", 60)
        )

        if not self.api_key:
            logger.error("Groq initialization failed: API key is not configured.")
            raise ValueError("Groq API key is not configured. Set GROQ_API_KEY.")

        if not self.model:
            logger.error("Groq initialization failed: model is not configured.")
            raise ValueError("Groq model is not configured. Set GROQ_MODEL.")

        logger.info(
            "Initializing Groq generator | model=%s | max_tokens=%s | timeout=%ss",
            self.model,
            self.max_tokens,
            self.timeout,
        )

        try:
            self.client = OpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=self.api_key,
                timeout=self.timeout,
            )

            logger.info(
                "Groq generator initialized successfully | model=%s",
                self.model,
            )

        except Exception:
            logger.exception(
                "Failed to initialize Groq client | model=%s",
                self.model,
            )
            raise

    def _call_model(self, model: str, prompt: str):
        return self.client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            max_tokens=self.max_tokens,
        )

    def generate(self, prompt: str) -> str:
        if not prompt or not prompt.strip():
            logger.warning("Groq generation skipped because prompt was empty.")
            raise ValueError("Prompt cannot be empty.")

        start_time = time.perf_counter()

        logger.info(
            "Groq generation started | model=%s | max_tokens=%s | prompt_length=%s",
            self.model,
            self.max_tokens,
            len(prompt),
        )

        # Retry once only when the provider returns an empty response.
        max_attempts = 2
        active_model = self.model

        for attempt in range(1, max_attempts + 1):
            attempt_start = time.perf_counter()

            logger.info(
                "Groq API attempt started | attempt=%s/%s | model=%s",
                attempt,
                max_attempts,
                active_model,
            )

            try:
                response = self._call_model(active_model, prompt)

            except AuthenticationError as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.error(
                    "Groq authentication failed | attempt=%s/%s | duration=%.3fs",
                    attempt,
                    max_attempts,
                    elapsed,
                )

                raise RuntimeError(
                    "Groq authentication failed. Check GROQ_API_KEY."
                ) from exc

            except RateLimitError as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.warning(
                    "Groq rate limit reached | attempt=%s/%s | duration=%.3fs",
                    attempt,
                    max_attempts,
                    elapsed,
                )

                raise RuntimeError(
                    "Groq rate limit reached. Please try again later."
                ) from exc

            except APITimeoutError as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.error(
                    "Groq request timed out | attempt=%s/%s | duration=%.3fs",
                    attempt,
                    max_attempts,
                    elapsed,
                )

                raise RuntimeError("Groq request timed out. Please try again.") from exc

            except APIConnectionError as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.error(
                    "Unable to connect to Groq | attempt=%s/%s | duration=%.3fs",
                    attempt,
                    max_attempts,
                    elapsed,
                )

                raise RuntimeError(
                    "Unable to connect to Groq. "
                    "Check your network connection and try again."
                ) from exc

            except BadRequestError as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.error(
                    "Groq rejected the request | attempt=%s/%s | duration=%.3fs | model=%s",
                    attempt,
                    max_attempts,
                    elapsed,
                    self.model,
                )

                raise RuntimeError(
                    "Groq rejected the request. "
                    "Check the selected model and request configuration."
                ) from exc

            except APIStatusError as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.error(
                    "Groq API request failed | http_status=%s | attempt=%s/%s | duration=%.3fs | model=%s",
                    exc.status_code,
                    attempt,
                    max_attempts,
                    elapsed,
                    active_model,
                )

                raise RuntimeError(
                    f"Groq request failed (HTTP {exc.status_code}). Please try again."
                ) from exc

            except Exception as exc:
                elapsed = time.perf_counter() - attempt_start

                logger.exception(
                    "Unexpected Groq generation failure | attempt=%s/%s | duration=%.3fs | model=%s",
                    attempt,
                    max_attempts,
                    elapsed,
                    self.model,
                )

                raise RuntimeError("Groq generation failed. Please try again.") from exc

            attempt_elapsed = time.perf_counter() - attempt_start

            if not response.choices:
                logger.warning(
                    "Groq returned no choices | attempt=%s/%s | duration=%.3fs | response_model=%s",
                    attempt,
                    max_attempts,
                    attempt_elapsed,
                    getattr(response, "model", None),
                )

                if attempt < max_attempts:
                    logger.info(
                        "Retrying Groq request because no choices were returned."
                    )
                    continue

                logger.error(
                    "Groq returned no choices after retrying | total_duration=%.3fs",
                    time.perf_counter() - start_time,
                )
                raise RuntimeError("Groq returned no choices.")

            choice = response.choices[0]
            message = getattr(choice, "message", None)
            answer = getattr(message, "content", None) if message is not None else None

            if answer is not None and str(answer).strip():
                total_elapsed = time.perf_counter() - start_time

                if total_elapsed > 10:
                    logger.warning(
                        "Slow Groq generation detected | duration=%.3fs | model=%s | attempts=%s",
                        total_elapsed,
                        active_model,
                        attempt,
                    )
                else:
                    logger.info(
                        "Groq generation completed successfully | duration=%.3fs | response_length=%s | attempts=%s",
                        total_elapsed,
                        len(str(answer)),
                        attempt,
                    )

                return str(answer).strip()

            finish_reason = getattr(choice, "finish_reason", None)

            logger.warning(
                "Groq returned an unusable empty content response | "
                "attempt=%s/%s | attempt_duration=%.3fs | finish_reason=%s | response_model=%s",
                attempt,
                max_attempts,
                attempt_elapsed,
                finish_reason,
                getattr(response, "model", None),
            )

            if attempt < max_attempts:
                logger.info(
                    "Retrying Groq request because assistant content was empty."
                )
                continue

            total_elapsed = time.perf_counter() - start_time

            logger.error(
                "Groq returned an empty response after retrying | total_duration=%.3fs | attempts=%s",
                total_elapsed,
                attempt,
            )

            raise RuntimeError("Groq returned an empty response.")

        # Defensive fallback; the loop should always return or raise.
        logger.error("Groq generation ended without a response.")
        raise RuntimeError("Groq generation failed. Please try again.")
