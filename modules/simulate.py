"""
Persona-Simulation (KI) — keine echte Marktforschung.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass

from llm.client import LLMClient
from llm.prompts.simulate import PERSONA_GENERATION_PROMPT, PERSONA_REACTION_PROMPT
from shared.exceptions import LLMError, LLMResponseParsingError
from shared.types import IdeaSummary, ProgressCallback, ResearchBundle, ResearchResult

logger = logging.getLogger(__name__)

DEFAULT_DISCLAIMER = (
    "⚠️ Hinweis: Diese Persona-Simulation ist KI-generiert und ersetzt keine echte Marktforschung."
)


@dataclass
class PersonaReaction:
    """Eine simulierte Reaktion einer Persona."""

    persona_name: str
    persona_card: str
    first_reaction: str
    would_pay: str
    biggest_concern: str
    would_recommend: str
    excitement_level: int
    follow_up_question: str


@dataclass
class SimulationResult:
    idea_id: int
    personas: list[str]
    reactions: list[PersonaReaction]
    disclaimer: str = DEFAULT_DISCLAIMER


class SimulationModule:
    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def _research_excerpt(self, research: ResearchBundle, max_chars: int = 14000) -> str:
        parts: list[str] = []
        for rr in research.results:
            if not isinstance(rr, ResearchResult):
                continue
            parts.append(f"### Tool: {rr.tool_name} (ok={rr.success})")
            if not rr.success and rr.error_message:
                parts.append(f"Fehler: {rr.error_message}")
            for stmt in rr.statements[:24]:
                parts.append(f"- {stmt.text[:500]}")
        tr = research.trend_radar
        parts.append(
            f"### Trend: {getattr(tr.verdict, 'value', tr.verdict)} — {tr.verdict_reasoning or ''}"
        )
        text = "\n".join(parts)
        if len(text) > max_chars:
            return text[: max_chars - 20] + "\n… [gekürzt]"
        return text

    def _pitch_block(self, summary: IdeaSummary) -> str:
        return (
            f"Problem: {summary.problem_statement}\n"
            f"Zielgruppe: {summary.target_audience}\n"
            f"Lösung: {summary.solution}\n"
            f"Monetarisierung: {summary.monetization}\n"
            f"Distribution: {summary.distribution_channel}\n"
            f"Vorteil: {summary.unfair_advantage}\n"
        )

    async def _generate_personas_json(
        self, summary: IdeaSummary, research: ResearchBundle
    ) -> list[dict]:
        user_msg = (
            f"{self._pitch_block(summary)}\n\n"
            f"## Recherche (Auszug)\n{self._research_excerpt(research)}\n"
        )
        raw = await self._llm.complete_structured(
            system_prompt=PERSONA_GENERATION_PROMPT,
            user_message=user_msg,
            task="summarize",
            max_tokens=4096,
        )
        personas = raw.get("personas")
        if not isinstance(personas, list) or not personas:
            raise LLMResponseParsingError("Persona-JSON: 'personas' fehlt oder leer.")
        return personas

    async def _reaction_for_persona(
        self, persona: dict, summary: IdeaSummary
    ) -> dict:
        persona_json = json.dumps(persona, ensure_ascii=False, indent=2)
        user_msg = (
            f"## Persona (JSON)\n{persona_json}\n\n"
            f"## Pitch\n{self._pitch_block(summary)}\n"
        )
        return await self._llm.complete_structured(
            system_prompt=PERSONA_REACTION_PROMPT,
            user_message=user_msg,
            task="summarize",
            max_tokens=2048,
        )

    async def run(
        self,
        idea_id: int,
        summary: IdeaSummary,
        research: ResearchBundle,
        progress: ProgressCallback | None,
    ) -> SimulationResult:
        async def notify(msg: str) -> None:
            if progress:
                await progress(msg)

        await notify("Personas werden erstellt…")
        personas_raw = await self._generate_personas_json(summary, research)
        random.shuffle(personas_raw)

        reactions: list[PersonaReaction] = []
        names: list[str] = []
        for i, p in enumerate(personas_raw):
            name = str(p.get("name") or f"Persona {i+1}")
            names.append(name)
            occ = str(p.get("occupation") or "")
            age = p.get("age", "")
            card = f"{name}, {age}, {occ}".strip()
            await notify(f"Reaktion von {name}…")
            try:
                react = await self._reaction_for_persona(p, summary)
            except (LLMError, LLMResponseParsingError) as exc:
                logger.warning("Reaktion für Persona fehlgeschlagen: %s", exc)
                react = {
                    "first_reaction": "(Konnte nicht erzeugt werden.)",
                    "would_pay": "maybe — unsicher wegen technischem Fehler.",
                    "biggest_concern": "Unklare Produktpositionierung.",
                    "would_recommend": "no — zu wenig Infos.",
                    "excitement_level": 2,
                    "follow_up_question": "Was unterscheidet euch konkret vom Status quo?",
                }
            el = react.get("excitement_level", 3)
            try:
                el_int = int(el)
            except (TypeError, ValueError):
                el_int = 3
            el_int = max(1, min(5, el_int))

            reactions.append(
                PersonaReaction(
                    persona_name=name,
                    persona_card=card,
                    first_reaction=str(react.get("first_reaction") or ""),
                    would_pay=str(react.get("would_pay") or ""),
                    biggest_concern=str(react.get("biggest_concern") or ""),
                    would_recommend=str(react.get("would_recommend") or ""),
                    excitement_level=el_int,
                    follow_up_question=str(react.get("follow_up_question") or ""),
                )
            )

        return SimulationResult(
            idea_id=idea_id,
            personas=names,
            reactions=reactions,
            disclaimer=DEFAULT_DISCLAIMER,
        )

    def format_telegram_output(self, result: SimulationResult) -> str:
        lines: list[str] = [
            "🎭 Persona-Simulation",
            "",
            result.disclaimer,
            "",
        ]
        for r in result.reactions:
            lines.append(f"━━ {r.persona_name} ━━")
            if r.persona_card:
                lines.append(r.persona_card)
                lines.append("")
            lines.append(f"Erste Reaktion: {r.first_reaction}")
            lines.append(f"Zahlungsbereitschaft: {r.would_pay}")
            lines.append(f"Groesstes Bedenken: {r.biggest_concern}")
            lines.append(f"Weiterempfehlung: {r.would_recommend}")
            lines.append(f"Excitement (1-5): {r.excitement_level}")
            lines.append(f"Frage an dich: {r.follow_up_question}")
            lines.append("")
        lines.append("—")
        lines.append("Simulation endet hier — bei echten Entscheidungen: User Research.")
        return "\n".join(lines)
