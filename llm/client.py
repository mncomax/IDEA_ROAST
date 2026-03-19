"""
Async wrapper around the Anthropic Python SDK for Claude API calls.
"""

from __future__ import annotations

import json
import logging

from anthropic import AsyncAnthropic, APIError, APITimeoutError

from shared.constants import LLM_MODEL_FAST
from shared.exceptions import LLMError, LLMResponseParsingError

logger = logging.getLogger(__name__)


class LLMClient:
    """Thin async client for Anthropic's Claude messages API."""

    def __init__(self, api_key: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> str:
        """Send a single user message with a system prompt and return the text response."""
        model = model or LLM_MODEL_FAST
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text
        except (APIError, APITimeoutError) as exc:
            logger.error("Anthropic API error: %s", exc)
            raise LLMError(str(exc)) from exc

    async def complete_structured(
        self,
        system_prompt: str,
        user_message: str,
        response_format: str = "json",
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> dict:
        """Like complete() but parses the response as JSON and returns a dict."""
        raw = await self.complete(
            system_prompt=system_prompt,
            user_message=user_message,
            model=model,
            max_tokens=max_tokens,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse LLM JSON response: %s", raw[:200])
            raise LLMResponseParsingError(
                f"Expected {response_format} but could not parse: {exc}"
            ) from exc
