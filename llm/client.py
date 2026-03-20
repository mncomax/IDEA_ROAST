"""
Multi-provider LLM client — routes tasks to the best model for the job.

Claude (Anthropic): Devils Advocate, Analysis, Out-of-Box — quality-critical reasoning.
GPT (OpenAI):       Brainstorm follow-ups, summaries, formatting — good enough, much cheaper.
"""

from __future__ import annotations

import json
import logging
from enum import Enum

from shared.exceptions import LLMError, LLMResponseParsingError

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


# Task-to-provider routing: quality where it matters, cost savings where it doesn't
TASK_ROUTING: dict[str, tuple[LLMProvider, str]] = {
    # --- Quality-critical (Claude) ---
    "devils_advocate":    (LLMProvider.ANTHROPIC, "claude-sonnet-4-20250514"),
    "analysis":           (LLMProvider.ANTHROPIC, "claude-sonnet-4-20250514"),
    "out_of_box":         (LLMProvider.ANTHROPIC, "claude-sonnet-4-20250514"),

    # --- Quality (Claude) for user-facing conversation ---
    "brainstorm":         (LLMProvider.ANTHROPIC, "claude-sonnet-4-20250514"),
    "summarize":          (LLMProvider.ANTHROPIC, "claude-sonnet-4-20250514"),
    "report_format":      (LLMProvider.OPENAI, "gpt-4o-mini"),
    "source_query":       (LLMProvider.OPENAI, "gpt-4o-mini"),
    "research_extract":   (LLMProvider.OPENAI, "gpt-4o-mini"),

    # --- Default fallback ---
    "default":            (LLMProvider.OPENAI, "gpt-4o-mini"),
}


class LLMClient:
    """Unified async LLM client that routes to Anthropic or OpenAI based on task."""

    def __init__(
        self,
        anthropic_api_key: str | None = None,
        openai_api_key: str | None = None,
    ) -> None:
        self._anthropic = None
        self._openai = None

        if anthropic_api_key:
            from anthropic import AsyncAnthropic
            self._anthropic = AsyncAnthropic(api_key=anthropic_api_key)

        if openai_api_key:
            from openai import AsyncOpenAI
            self._openai = AsyncOpenAI(api_key=openai_api_key)

        if not self._anthropic and not self._openai:
            raise LLMError("Mindestens ein LLM-Provider (Anthropic oder OpenAI) muss konfiguriert sein.")

    def _resolve_provider(self, task: str | None) -> tuple[LLMProvider, str]:
        """Determine provider and model for a given task."""
        if task and task in TASK_ROUTING:
            provider, model = TASK_ROUTING[task]
        else:
            provider, model = TASK_ROUTING["default"]

        if provider == LLMProvider.ANTHROPIC and not self._anthropic:
            logger.info("Anthropic not configured, falling back to OpenAI for task=%s", task)
            provider = LLMProvider.OPENAI
            model = "gpt-4o-mini"
        elif provider == LLMProvider.OPENAI and not self._openai:
            logger.info("OpenAI not configured, falling back to Anthropic for task=%s", task)
            provider = LLMProvider.ANTHROPIC
            model = "claude-sonnet-4-20250514"

        return provider, model

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        task: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> str:
        """Route a completion to the right provider based on task type."""
        provider, resolved_model = self._resolve_provider(task)
        if model:
            resolved_model = model

        logger.debug("LLM call: task=%s provider=%s model=%s", task, provider, resolved_model)

        if provider == LLMProvider.ANTHROPIC:
            return await self._complete_anthropic(system_prompt, user_message, resolved_model, max_tokens)
        return await self._complete_openai(system_prompt, user_message, resolved_model, max_tokens)

    async def complete_structured(
        self,
        system_prompt: str,
        user_message: str,
        task: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> dict:
        """Like complete() but parses the response as JSON."""
        raw = await self.complete(
            system_prompt=system_prompt,
            user_message=user_message,
            task=task,
            model=model,
            max_tokens=max_tokens,
        )

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:]  # remove opening ```json
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse LLM JSON response: %s", raw[:300])
            raise LLMResponseParsingError(
                f"JSON parse error: {exc}"
            ) from exc

    # --- Provider implementations ---

    async def _complete_anthropic(
        self, system_prompt: str, user_message: str, model: str, max_tokens: int
    ) -> str:
        from anthropic import APIError, APITimeoutError
        try:
            response = await self._anthropic.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text
        except (APIError, APITimeoutError) as exc:
            logger.error("Anthropic API error: %s", exc)
            raise LLMError(f"Anthropic: {exc}") from exc

    async def _complete_openai(
        self, system_prompt: str, user_message: str, model: str, max_tokens: int
    ) -> str:
        from openai import APIError, APITimeoutError
        try:
            response = await self._openai.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            return response.choices[0].message.content
        except (APIError, APITimeoutError) as exc:
            logger.error("OpenAI API error: %s", exc)
            raise LLMError(f"OpenAI: {exc}") from exc
