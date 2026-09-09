import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()


@dataclass
class CortexAgentResponse:
    """Result returned by Cortex Agent."""

    answer: str
    request_id: Optional[str] = None
    raw_response: Optional[dict] = None
    used_tools: list[str] | None = None


class CortexAgentService:
    """
    Service responsible for communicating with the
    Snowflake Cortex Agent object.

    This is intentionally separate from the existing
    Policy RAG / provider-generation architecture.
    """

    def __init__(
        self,
        database: str = "RETAIL_ASSIST_DB",
        schema: str = "RETAIL_ASSIST",
        agent_name: str = "RETAIL_ASSIST_AGENT",
    ):
        self.database = database
        self.schema = schema
        self.agent_name = agent_name

        self.account = os.environ.get("SNOWFLAKE_ACCOUNT")
        self.pat = os.environ.get("SNOWFLAKE_PAT")

        if not self.account:
            raise ValueError(
                "Missing Snowflake environment variable: SNOWFLAKE_ACCOUNT"
            )

        if not self.pat:
            raise ValueError("Missing Snowflake environment variable: SNOWFLAKE_PAT")

        self.host = os.environ.get(
            "SNOWFLAKE_HOST",
            f"{self.account}.snowflakecomputing.com",
        )

        # IMPORTANT:
        # This is the endpoint for an existing Agent object.
        self.url = (
            f"https://{self.host}"
            f"/api/v2/databases/{self.database}"
            f"/schemas/{self.schema}"
            f"/agents/{self.agent_name}:run"
        )

    @staticmethod
    def _clean_agent_answer_text(answer: str) -> str:
        """
        Remove only obvious trailing continuation fragments from
        the final Agent answer.

        Cortex Agent can occasionally return a final standalone fragment
        such as "What" after the substantive answer. This method removes
        that fragment without modifying normal sentence content.
        """
        if not answer:
            return ""

        text = str(answer).strip()

        # Remove a dangling continuation fragment at the very end.
        # Cortex Agent can occasionally append a final partial "What"
        # either on a new line or after the completed sentence.
        cleaned_text = re.sub(
            r"(?i)(?:\s*(?:<br>\s*)?)[.!,:;—–-]?\s*(?:What|Why|How|Where|When|Which|Who)[?.!…]?\s*$",
            "",
            text,
        )

        if cleaned_text != text:
            logger.debug(
                "Removed dangling Cortex Agent continuation fragment from final answer."
            )

        text = cleaned_text

        # Normalize whitespace after removing a trailing fragment.
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()

    @staticmethod
    def _append_unique_text(
        answer_parts: list[str],
        value: object,
    ) -> None:
        """
        Append meaningful answer text while preserving order and avoiding
        duplicate/standalone continuation fragments.
        """
        if value is None:
            return

        text = str(value).strip()

        if not text:
            return

        # Cortex Agent can occasionally emit a final partial continuation
        # as its own text block. Do not let that fragment become part of the
        # user-facing answer.
        if re.fullmatch(
            r"(?:What|Why|How|Where|When|Which|Who)[?.!…]?",
            text,
            flags=re.IGNORECASE,
        ):
            logger.debug(
                "Ignoring standalone Cortex Agent continuation fragment: %r",
                text,
            )
            return

        if text not in answer_parts:
            answer_parts.append(text)

    def ask(self, question: str) -> CortexAgentResponse:
        """Send a natural-language question to the Cortex Agent."""
        if not question or not question.strip():
            raise ValueError("Question cannot be empty.")

        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": question.strip(),
                        }
                    ],
                }
            ],
            "stream": False,
        }

        headers = {
            "Authorization": f"Bearer {self.pat}",
            "X-Snowflake-Authorization-Token-Type": ("PROGRAMMATIC_ACCESS_TOKEN"),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            response = requests.post(
                self.url,
                json=payload,
                headers=headers,
                timeout=120,
            )
        except requests.RequestException as exc:
            logger.exception("Cortex Agent request failed.")
            raise RuntimeError("Unable to connect to Snowflake Cortex Agent.") from exc

        request_id = response.headers.get("X-Snowflake-Request-Id")

        # ---------------------------------------------------------
        # HTTP ERROR
        # ---------------------------------------------------------
        if response.status_code >= 400:
            logger.error(
                "Cortex Agent returned HTTP %s: %s",
                response.status_code,
                response.text,
            )

            try:
                error_body = response.json()
            except ValueError:
                error_body = {}

            if isinstance(error_body, dict):
                message = (
                    error_body.get("message")
                    or error_body.get("error")
                    or response.text
                )
            else:
                message = response.text

            raise RuntimeError(
                f"Cortex Agent request failed: {message or 'Unknown error'}"
            )

        # ---------------------------------------------------------
        # PARSE NON-STREAMING JSON RESPONSE
        # ---------------------------------------------------------
        try:
            data = response.json()
        except ValueError as exc:
            logger.error(
                "Cortex Agent returned invalid JSON: %s",
                response.text[:5000],
            )
            raise RuntimeError(
                "Cortex Agent returned an invalid JSON response."
            ) from exc

        # ---------------------------------------------------------
        # RESPONSE CONTENT
        # ---------------------------------------------------------
        answer_parts: list[str] = []
        content = data.get("content", [])

        # ---------------------------------------------------------
        # EXTRACT ACTUAL TOOLS USED BY CORTEX AGENT
        # ---------------------------------------------------------
        used_tools: list[str] = []

        def collect_tools(value) -> None:
            """Recursively find Cortex Agent tool_use/tool_result objects."""
            if isinstance(value, dict):
                value_type = str(value.get("type") or "").strip().lower()

                # Standard response content item:
                #
                # {
                #     "type": "tool_use",
                #     "tool_use": {
                #         "type": "cortex_search",
                #         "name": "PolicySearch"
                #     }
                # }

                if value_type in {"tool_use", "tool_result"}:
                    nested = value.get(value_type)

                    if isinstance(nested, dict):
                        tool_name = str(
                            nested.get("name") or nested.get("tool_name") or ""
                        ).strip()

                        tool_type = str(
                            nested.get("type") or nested.get("tool_type") or ""
                        ).strip()

                        if tool_name:
                            used_tools.append(tool_name)
                        elif tool_type:
                            used_tools.append(tool_type)

                # Also handle direct tool objects.
                direct_name = str(value.get("tool_name") or "").strip()

                direct_type = str(value.get("tool_type") or "").strip()

                if direct_name:
                    used_tools.append(direct_name)
                elif direct_type:
                    used_tools.append(direct_type)

                for nested_value in value.values():
                    collect_tools(nested_value)

            elif isinstance(value, list):
                for item in value:
                    collect_tools(item)

        collect_tools(data)

        # Preserve order and remove duplicates.
        used_tools = list(dict.fromkeys(used_tools))

        # ---------------------------------------------------------
        # EXTRACT TOP-LEVEL CONTENT TEXT
        # ---------------------------------------------------------
        if isinstance(content, list):
            for content_block in content:
                if not isinstance(content_block, dict):
                    continue

                block_type = str(content_block.get("type") or "").strip().lower()

                if block_type == "text":
                    self._append_unique_text(
                        answer_parts,
                        content_block.get("text"),
                    )

        # ---------------------------------------------------------
        # EXTRACT ASSISTANT / MESSAGE CONTENT
        # ---------------------------------------------------------
        message = data.get("message")

        if isinstance(message, dict):
            message_content = message.get("content", [])

            if isinstance(message_content, list):
                for content_block in message_content:
                    if not isinstance(content_block, dict):
                        continue

                    block_type = str(content_block.get("type") or "").strip().lower()

                    if block_type == "text":
                        self._append_unique_text(
                            answer_parts,
                            content_block.get("text"),
                        )

            elif isinstance(message_content, str):
                self._append_unique_text(
                    answer_parts,
                    message_content,
                )

        # ---------------------------------------------------------
        # FALLBACK
        # ---------------------------------------------------------
        if not answer_parts:
            self._append_unique_text(
                answer_parts,
                data.get("text"),
            )

        # ---------------------------------------------------------
        # FINAL ANSWER
        # ---------------------------------------------------------
        answer = self._clean_agent_answer_text("\n".join(answer_parts))

        if not answer:
            logger.error(
                "Cortex Agent returned no answer text. Response: %s",
                json.dumps(
                    data,
                    indent=2,
                    default=str,
                )[:5000],
            )

            raise RuntimeError(
                "Cortex Agent returned a response, "
                "but no answer text could be extracted."
            )

        return CortexAgentResponse(
            answer=answer,
            request_id=request_id,
            raw_response=data,
            used_tools=used_tools,
        )
