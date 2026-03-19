"""
Brainstorm module — adaptive conversation flow.

The bot first reflects on the idea to show understanding, then asks
targeted follow-up questions specific to the use case. Questions that
were already answered get skipped automatically.
"""

from __future__ import annotations

import logging

from llm.client import LLMClient
from llm.prompts.brainstorm import (
    BRAINSTORM_SYSTEM_PROMPT,
    REFLECT_SYSTEM_PROMPT,
    SUMMARIZE_SYSTEM_PROMPT,
)
from shared.constants import BRAINSTORM_QUESTIONS
from shared.types import (
    BrainstormAnswers,
    BrainstormState,
    ConversationContext,
    IdeaSummary,
)

logger = logging.getLogger(__name__)

# Which brainstorm states map to which answer field
STATE_ANSWER_FIELD: dict[BrainstormState, str] = {
    BrainstormState.AWAITING_IDEA: "raw_idea",
    BrainstormState.ASKING_PERSONA: "persona",
    BrainstormState.ASKING_CURRENT_SOLUTION: "current_solution",
    BrainstormState.ASKING_SWITCH_TRIGGER: "switch_trigger",
    BrainstormState.ASKING_MONETIZATION: "monetization",
    BrainstormState.ASKING_DISTRIBUTION: "distribution",
}

# Ordered list of question topics to cover (after initial reflection)
QUESTION_TOPICS: list[tuple[BrainstormState, str, str]] = [
    (
        BrainstormState.ASKING_PERSONA,
        "persona",
        "Wer genau hat dieses Problem? Konkrete Zielgruppe.",
    ),
    (
        BrainstormState.ASKING_CURRENT_SOLUTION,
        "current_solution",
        "Wie loesen die Leute das Problem heute? Was nervt daran?",
    ),
    (
        BrainstormState.ASKING_SWITCH_TRIGGER,
        "switch_trigger",
        "Warum wuerden sie wechseln? Was ist der Ausloeser?",
    ),
    (
        BrainstormState.ASKING_MONETIZATION,
        "monetization",
        "Wie wird damit Geld verdient?",
    ),
    (
        BrainstormState.ASKING_DISTRIBUTION,
        "distribution",
        "Wie finden Kunden das Produkt?",
    ),
]


def _build_conversation_log(answers: BrainstormAnswers) -> str:
    """Render the conversation so far as a readable log for the LLM."""
    parts: list[str] = []
    mapping = [
        ("Idee", answers.raw_idea),
        ("Zielgruppe/Persona", answers.persona),
        ("Aktuelle Loesung & Pain", answers.current_solution),
        ("Wechselgrund/Trigger", answers.switch_trigger),
        ("Monetarisierung", answers.monetization),
        ("Distribution/Kundenakquise", answers.distribution),
    ]
    for label, value in mapping:
        if value:
            parts.append(f"- {label}: {value}")
    return "\n".join(parts) if parts else "(noch nichts)"


def _find_next_open_topic(
    answers: BrainstormAnswers,
) -> tuple[BrainstormState, str] | None:
    """Find the next topic that hasn't been covered yet."""
    for state, field, topic_hint in QUESTION_TOPICS:
        if not getattr(answers, field, ""):
            return state, topic_hint
    return None


class BrainstormModule:
    """Adaptive brainstorm conversation — reflects first, then asks smart questions."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    async def process_message(
        self,
        context: ConversationContext,
        user_message: str,
    ) -> tuple[str, BrainstormState]:
        current_state = context.brainstorm_state
        answers = context.brainstorm_answers

        # Store the answer in the right field
        field_name = STATE_ANSWER_FIELD.get(current_state)
        if field_name:
            setattr(answers, field_name, user_message)

        # First message: reflect on the idea, then ask first targeted question
        if current_state == BrainstormState.AWAITING_IDEA:
            return await self._reflect_and_ask(answers, user_message)

        # Subsequent messages: find next open topic and ask contextually
        return await self._continue_conversation(answers, user_message)

    async def _reflect_and_ask(
        self, answers: BrainstormAnswers, idea_text: str
    ) -> tuple[str, BrainstormState]:
        """After the initial idea: reflect understanding, then ask first real question."""
        next_topic = _find_next_open_topic(answers)
        if not next_topic:
            return "Alles klar, ich erstelle die Zusammenfassung...", BrainstormState.SUMMARIZING

        next_state, topic_hint = next_topic

        prompt = (
            f"Der Gruender hat folgende Idee beschrieben:\n\n"
            f'"{idea_text}"\n\n'
            f"Zeig dass du die Idee verstanden hast, dann stell eine gezielte "
            f"Frage in Richtung: {topic_hint}"
        )

        response = await self._llm.complete(
            system_prompt=REFLECT_SYSTEM_PROMPT,
            user_message=prompt,
            task="brainstorm",
        )
        return response, next_state

    async def _continue_conversation(
        self, answers: BrainstormAnswers, last_answer: str
    ) -> tuple[str, BrainstormState]:
        """Ask the next relevant question based on what's still open."""
        next_topic = _find_next_open_topic(answers)
        if not next_topic:
            return await self._wrap_up(answers), BrainstormState.SUMMARIZING

        next_state, topic_hint = next_topic
        conversation = _build_conversation_log(answers)

        prompt = (
            f"Gespraechsverlauf bisher:\n{conversation}\n\n"
            f"Letzte Antwort des Users:\n{last_answer}\n\n"
            f"Noch offenes Thema: {topic_hint}\n\n"
            f"Reagiere kurz auf die letzte Antwort, dann frag gezielt in Richtung "
            f"des offenen Themas — aber spezifisch fuer diese Idee, nicht generisch."
        )

        response = await self._llm.complete(
            system_prompt=BRAINSTORM_SYSTEM_PROMPT,
            user_message=prompt,
            task="brainstorm",
        )
        return response, next_state

    async def _wrap_up(self, answers: BrainstormAnswers) -> str:
        """All topics covered — short transition to summary."""
        conversation = _build_conversation_log(answers)

        prompt = (
            f"Gespraechsverlauf:\n{conversation}\n\n"
            "Alle wichtigen Themen sind abgedeckt. "
            "Sag kurz (1-2 Saetze) dass du jetzt die Zusammenfassung erstellst. "
            "Kein neues Thema."
        )

        return await self._llm.complete(
            system_prompt=BRAINSTORM_SYSTEM_PROMPT,
            user_message=prompt,
            task="brainstorm",
        )

    async def generate_summary(self, context: ConversationContext) -> IdeaSummary:
        """Create a structured IdeaSummary from the collected conversation."""
        answers = context.brainstorm_answers
        conversation = _build_conversation_log(answers)

        prompt = (
            f"Hier ist das gesamte Brainstorm-Gespraech:\n{conversation}\n\n"
            "Erstelle die JSON-Zusammenfassung."
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
