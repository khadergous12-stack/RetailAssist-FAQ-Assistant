from __future__ import annotations

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


class OpenAIGenerator:
    """
    OpenRouter-backed implementation of the provider-neutral
    Generator contract.

    OpenRouter exposes an OpenAI-compatible API.
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
            raise ValueError(
                "OpenRouter API key is not configured. Set OPENROUTER_API_KEY."
            )

        if not self.model:
            raise ValueError(
                "OpenRouter model is not configured. Set OPENROUTER_MODEL."
            )

        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key,
            timeout=settings.openrouter_timeout,
        )

    def generate(self, prompt: str) -> str:
        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

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
            raise RuntimeError(
                "OpenRouter authentication failed. Check OPENROUTER_API_KEY."
            ) from exc

        except RateLimitError as exc:
            raise RuntimeError(
                "OpenRouter rate limit reached. Please try again later."
            ) from exc

        except APITimeoutError as exc:
            raise RuntimeError(
                "OpenRouter request timed out. Please try again."
            ) from exc

        except APIConnectionError as exc:
            raise RuntimeError(
                "Unable to connect to OpenRouter. "
                "Check your network connection and try again."
            ) from exc

        except BadRequestError as exc:
            raise RuntimeError(
                "OpenRouter rejected the request. "
                "Check the selected model and request configuration."
            ) from exc

        except APIStatusError as exc:
            raise RuntimeError(
                f"OpenRouter request failed (HTTP {exc.status_code}). Please try again."
            ) from exc

        except Exception as exc:
            raise RuntimeError(
                "OpenRouter generation failed. Please try again."
            ) from exc

        if not response.choices:
            raise RuntimeError("OpenRouter returned no choices.")

        answer = response.choices[0].message.content

        if not answer:
            raise RuntimeError("OpenRouter returned an empty response.")

        return str(answer).strip()
