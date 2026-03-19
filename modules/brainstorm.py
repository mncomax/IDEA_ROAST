"""
Brainstorm module — orchestrates the sokratische Fragen flow.

Receives plain text, returns plain text. No Telegram specifics here.
"""

from __future__ import annotations

import logging

from llm.client import LLMClient
from llm.prompts.brainstorm import BRAINSTORM_SYSTEM_PROMPT, SUMMARIZE_SYSTEM_PROMPT
from shared.constants import BRAINSTORM_QUESTIONS
from shared.types import (
    BrainstormAnswers,
    BrainstormState,
    ConversationContext,
    IdeaSummary,
)

logger = logging.getLogger(__name__)

STATE_ANSWER_FIELD: dict[BrainstormState, str] = {
    BrainstormState.AWAITING_IDEA: "raw_idea",
    BrainstormState.ASKING_PERSONA: "persona",
    BrainstormState.ASKING_CURRENT_SOLUTION: "current_solution",
    BrainstormState.ASKING_SWITCH_TRIGGER: "switch_trigger",
    BrainstormState.ASKING_MONETIZATION: "monetization",
    BrainstormState.ASKING_DISTRIBUTION: "distribution",
}

STATE_FLOW: list[BrainstormState] = [
    BrainstormState.AWAITING_IDEA,
    BrainstormState.ASKING_PERSONA,
    BrainstormState.ASKING_CURRENT_SOLUTION,
    BrainstormState.ASKING_SWITCH_TRIGGER,
    BrainstormState.ASKING_MONETIZATION,
    BrainstormState.ASKING_DISTRIBUTION,
    BrainstormState.SUMMARIZING,
]


def _next_state(current: BrainstormState) -> BrainstormState:
    idx = STATE_FLOW.index(current)
    return STATE_FLOW[idx + 1]


def _build_history_text(answers: BrainstormAnswers) -> str:
    """Render collected answers into a compact text block for the LLM."""
    parts: list[str] = []
    labels = [
        ("Idee", answers.raw_idea),
        ("Zielgruppe", answers.persona),
        ("Aktuelle Loesung", answers.current_solution),
        ("Wechselgrund", answers.switch_trigger),
        ("Monetarisierung", answers.monetization),
        ("Distribution", answers.distribution),
    ]
    for label, value in labels:
        if value:
            parts.append(f"- {label}: {value}")
    return "\n".join(parts)


class BrainstormModule:
    """Drives the 6-question brainstorm conversation."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    async def process_message(
        self,
        context: ConversationContext,
        user_message: str,
    ) -> tuple[str, BrainstormState]:
        """Store the answer, advance state, generate a contextual follow-up question.

        Returns (bot_response_text, new_brainstorm_state).
        """
        current_state = context.brainstorm_state
        answers = context.brainstorm_answers

        field_name = STATE_ANSWER_FIELD.get(current_state)
        if field_name:
            setattr(answers, field_name, user_message)

        new_state = _next_state(current_state)

        if new_state == BrainstormState.SUMMARIZING:
            return await self._final_transition_response(answers), new_state

        next_topic = BRAINSTORM_QUESTIONS.get(new_state.value, "")
        history = _build_history_text(answers)

        user_prompt = (
            f"Bisherige Antworten:\n{history}\n\n"
            f"Letzte Antwort des Users:\n{user_message}\n\n"
            f"Thematische Richtung der naechsten Frage:\n{next_topic}\n\n"
            "Formuliere jetzt die naechste Frage — kurz, natuerlich, auf den User eingehend."
        )

        bot_response = await self._llm.complete(
            system_prompt=BRAINSTORM_SYSTEM_PROMPT,
            user_message=user_prompt,
            task="brainstorm",
        )
        return bot_response, new_state

    async def generate_summary(self, context: ConversationContext) -> IdeaSummary:
        """Create a structured IdeaSummary from all collected answers."""
        answers = context.brainstorm_answers
        history = _build_history_text(answers)

        user_prompt = (
            f"Hier sind alle Antworten aus dem Brainstorm:\n{history}\n\n"
            "Erstelle die JSON-Zusammenfassung."
        )

        data = await self._llm.complete_structured(
            system_prompt=SUMMARIZE_SYSTEM_PROMPT,
            user_message=user_prompt,
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

    async def _final_transition_response(self, answers: BrainstormAnswers) -> str:
        """Generate a short wrap-up message before summarizing."""
        history = _build_history_text(answers)

        user_prompt = (
            f"Bisherige Antworten:\n{history}\n\n"
            "Der User hat alle 6 Fragen beantwortet. "
            "Sag kurz (1-2 Saetze), dass du jetzt die Zusammenfassung erstellst. "
            "Kein neues Thema aufmachen."
        )

        return await self._llm.complete(
            system_prompt=BRAINSTORM_SYSTEM_PROMPT,
            user_message=user_prompt,
            task="brainstorm",
        )
