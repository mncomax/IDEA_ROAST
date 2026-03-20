"""
Brainstorm module — free-flowing sparring conversation.

Instead of asking a rigid list of questions, the bot has a natural
conversation with the user about their idea. After enough exchanges
it wraps up and creates a structured summary.
"""

from __future__ import annotations

import logging

from llm.client import LLMClient
from llm.prompts.brainstorm import (
    BRAINSTORM_SYSTEM_PROMPT,
    REFLECT_SYSTEM_PROMPT,
    SUMMARIZE_SYSTEM_PROMPT,
)
from shared.types import (
    BrainstormAnswers,
    BrainstormState,
    ConversationContext,
    IdeaSummary,
)

logger = logging.getLogger(__name__)

MAX_EXCHANGES = 5


def _build_conversation_log(answers: BrainstormAnswers) -> str:
    """Render the full conversation as readable text for the LLM."""
    if not answers.conversation_log:
        if answers.raw_idea:
            return f"User: {answers.raw_idea}"
        return "(noch nichts)"

    parts: list[str] = []
    for role, msg in answers.conversation_log:
        label = "User" if role == "user" else "Bot"
        parts.append(f"{label}: {msg}")
    return "\n\n".join(parts)


class BrainstormModule:
    """Free-form brainstorm conversation that acts as a sparring partner."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    async def process_message(
        self,
        context: ConversationContext,
        user_message: str,
    ) -> tuple[str, BrainstormState]:
        answers = context.brainstorm_answers
        current_state = context.brainstorm_state

        if current_state == BrainstormState.AWAITING_IDEA:
            answers.raw_idea = user_message
            answers.conversation_log.append(("user", user_message))
            return await self._reflect_and_engage(answers, user_message)

        answers.conversation_log.append(("user", user_message))
        answers.exchange_count += 1

        if answers.exchange_count >= MAX_EXCHANGES:
            return await self._wrap_up(answers), BrainstormState.SUMMARIZING

        return await self._continue_sparring(answers, user_message)

    async def _reflect_and_engage(
        self, answers: BrainstormAnswers, idea_text: str
    ) -> tuple[str, BrainstormState]:
        """First response: show understanding and start the real conversation."""
        prompt = (
            f"Der Gruender hat folgende Idee beschrieben:\n\n"
            f'"{idea_text}"\n\n'
            "Zeig dass du die Idee verstehst, gib einen echten Gedanken dazu, "
            "und stell eine Frage die zeigt dass du mitdenkst."
        )

        response = await self._llm.complete(
            system_prompt=REFLECT_SYSTEM_PROMPT,
            user_message=prompt,
            task="brainstorm",
        )
        answers.conversation_log.append(("bot", response))
        return response, BrainstormState.CONVERSING

    async def _continue_sparring(
        self, answers: BrainstormAnswers, last_answer: str
    ) -> tuple[str, BrainstormState]:
        """Continue the natural conversation. LLM decides what to explore."""
        conversation = _build_conversation_log(answers)
        remaining = MAX_EXCHANGES - answers.exchange_count

        prompt = (
            f"Bisheriges Gespraech:\n{conversation}\n\n"
            f"Verbleibende Antworten: {remaining}\n\n"
            "Reagiere auf das Gesagte und fuehr das Gespraech weiter. "
            "Wenn du merkst dass die wichtigsten Punkte geklaert sind, "
            "kannst du auch frueher zur Zusammenfassung ueberleiten."
        )

        response = await self._llm.complete(
            system_prompt=BRAINSTORM_SYSTEM_PROMPT,
            user_message=prompt,
            task="brainstorm",
        )
        answers.conversation_log.append(("bot", response))

        wrap_indicators = [
            "zusammenfassung", "fasse zusammen", "genug besprochen",
            "alles klar soweit", "erstelle die zusammenfassung",
        ]
        if any(ind in response.lower() for ind in wrap_indicators):
            return response, BrainstormState.SUMMARIZING

        return response, BrainstormState.CONVERSING

    async def _wrap_up(self, answers: BrainstormAnswers) -> str:
        """Max exchanges reached — short transition to summary."""
        conversation = _build_conversation_log(answers)

        prompt = (
            f"Gespraech:\n{conversation}\n\n"
            "Die wichtigsten Punkte sind besprochen. "
            "Sag in 1-2 Saetzen was du mitnimmst und dass du jetzt "
            "die Zusammenfassung erstellst."
        )

        response = await self._llm.complete(
            system_prompt=BRAINSTORM_SYSTEM_PROMPT,
            user_message=prompt,
            task="brainstorm",
        )
        answers.conversation_log.append(("bot", response))
        return response

    async def generate_summary(self, context: ConversationContext) -> IdeaSummary:
        """Create a structured IdeaSummary from the full conversation."""
        answers = context.brainstorm_answers
        conversation = _build_conversation_log(answers)

        prompt = (
            f"Hier ist das gesamte Brainstorm-Gespraech:\n\n{conversation}\n\n"
            "Erstelle die JSON-Zusammenfassung. Leite alle Felder aus dem "
            "Gespraech ab — wenn etwas nicht explizit besprochen wurde, "
            "schreib 'Nicht spezifiziert'."
        )

        data = await self._llm.complete_structured(
            system_prompt=SUMMARIZE_SYSTEM_PROMPT,
            user_message=prompt,
            task="summarize",
        )

        return IdeaSummary(
            problem_statement=data.get("problem_statement", ""),
            target_audience=data.get("target_audience", ""),
            solution=data.get("solution", ""),
            monetization=data.get("monetization", ""),
            distribution_channel=data.get("distribution_channel", ""),
            unfair_advantage=data.get("unfair_advantage", ""),
            raw_answers=answers,
        )
