"""
User profile module — learns founder traits from conversations and free-text updates.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from db.repository import Repository
from llm.client import LLMClient
from shared.exceptions import LLMError, LLMResponseParsingError
from shared.types import (
    AnalysisResult,
    BrainstormAnswers,
    CategoryScore,
    IdeaSummary,
    UserProfile,
)

logger = logging.getLogger(__name__)

_RISK_ALLOWED = frozenset({"conservative", "moderate", "aggressive"})

_EXTRACTION_SYSTEM = """Du extrahierst strukturierte Profil-Updates aus Text über eine(n) Gründer(in).
Antworte NUR mit gültigem JSON, kein Markdown, keine Erklärung außerhalb des JSON.

Schema:
{
  "skills": ["kurze Fähigkeiten, z.B. Fullstack, Marketing"],
  "industries": ["Branchen / Domänen"],
  "preferred_stack": ["Technologien, Frameworks, Tools"],
  "risk_appetite": "conservative" | "moderate" | "aggressive" | null,
  "weekly_hours": Zahl oder null,
  "notes_for_user": "kurze deutsche Bestätigung was du erkannt hast (1 Satz)"
}

Leere Arrays wenn nichts Neues. Nutze null wenn ein Feld nicht erwähnt wird.
"""

_CONVERSATION_SYSTEM = """Du analysierst eine validierte Geschäftsidee und optional eine Bewertung.
Leite daraus plausible Hinweise auf den/die Gründer(in) ab: Skills, Branchenkenntnis, Tech-Stack, Risikobereitschaft, verfügbare Zeit.

Antworte NUR mit gültigem JSON:
{
  "skills": [],
  "industries": [],
  "preferred_stack": [],
  "risk_appetite": "conservative" | "moderate" | "aggressive" | null,
  "weekly_hours": Zahl oder null
}

Nur Einträge aufnehmen, die sich aus dem Inhalt begründen lassen. Keine Erfindungen."""


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if value is None or value == "":
        return datetime.utcnow()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.utcnow()


def _merge_str_lists(existing: list[str], new: list[Any]) -> list[str]:
    seen = {s.lower().strip() for s in existing if s and str(s).strip()}
    out = list(existing)
    for item in new:
        if item is None:
            continue
        t = str(item).strip()
        if not t:
            continue
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def _profile_from_row(row: dict[str, Any], telegram_id: int) -> UserProfile:
    skills = row.get("skills_json") or []
    industries = row.get("industries_json") or []
    stack = row.get("preferred_stack_json") or []
    if isinstance(skills, str):
        skills = json.loads(skills) if skills else []
    if isinstance(industries, str):
        industries = json.loads(industries) if industries else []
    if isinstance(stack, str):
        stack = json.loads(stack) if stack else []

    return UserProfile(
        telegram_id=telegram_id,
        name=(row.get("name") or "") or "",
        skills=list(skills) if isinstance(skills, list) else [],
        industries=list(industries) if isinstance(industries, list) else [],
        risk_appetite=(row.get("risk_appetite") or "moderate") or "moderate",
        weekly_hours=float(row.get("weekly_hours") or 0.0),
        preferred_stack=list(stack) if isinstance(stack, list) else [],
        created_at=_parse_dt(row.get("created_at")),
    )


def _analysis_excerpt(analysis: AnalysisResult | None) -> str:
    if analysis is None:
        return "(keine Analyse)"
    lines: list[str] = []
    lines.append(f"Empfehlung: {analysis.recommendation.value}")
    if analysis.recommendation_reasoning:
        lines.append(f"Begründung: {analysis.recommendation_reasoning[:800]}")
    for s in analysis.scores[:12]:
        if isinstance(s, CategoryScore):
            lines.append(f"- {s.category}: {s.level.value} — {s.reasoning[:400]}")
    return "\n".join(lines)


def _idea_payload(idea_summary: IdeaSummary) -> str:
    a = idea_summary.raw_answers
    return (
        f"Problem: {idea_summary.problem_statement}\n"
        f"Zielgruppe: {idea_summary.target_audience}\n"
        f"Lösung: {idea_summary.solution}\n"
        f"Monetarisierung: {idea_summary.monetization}\n"
        f"Distribution: {idea_summary.distribution_channel}\n"
        f"Vorteil: {idea_summary.unfair_advantage}\n"
        f"Roh-Idee: {a.raw_idea}\n"
        f"Persona-Antwort: {a.persona}\n"
        f"Aktuelle Lösung: {a.current_solution}\n"
    )


class ProfileModule:
    def __init__(self, llm: LLMClient, repo: Repository) -> None:
        self._llm = llm
        self._repo = repo

    async def get_or_create_profile(self, telegram_id: int, name: str = "") -> UserProfile:
        row = await self._repo.get_profile(telegram_id)
        if row is None:
            await self._repo.save_or_update_profile(
                telegram_id,
                name=name or "",
                skills_json=[],
                industries_json=[],
                preferred_stack_json=[],
            )
            row = await self._repo.get_profile(telegram_id)
            if row is None:
                logger.error("Profile insert failed for telegram_id=%s", telegram_id)
                return UserProfile(telegram_id=telegram_id, name=name or "")
        return _profile_from_row(row, telegram_id)

    def _apply_structured_updates(
        self,
        base: UserProfile,
        data: dict[str, Any],
        *,
        include_notes: bool = False,
    ) -> tuple[UserProfile, str]:
        skills = _merge_str_lists(base.skills, data.get("skills") or [])
        industries = _merge_str_lists(base.industries, data.get("industries") or [])
        stack = _merge_str_lists(base.preferred_stack, data.get("preferred_stack") or [])

        risk = base.risk_appetite
        raw_risk = data.get("risk_appetite")
        if isinstance(raw_risk, str) and raw_risk.lower() in _RISK_ALLOWED:
            risk = raw_risk.lower()

        hours = base.weekly_hours
        wh = data.get("weekly_hours")
        if isinstance(wh, (int, float)) and wh >= 0:
            hours = float(wh)

        notes = ""
        if include_notes:
            n = data.get("notes_for_user")
            if isinstance(n, str) and n.strip():
                notes = n.strip()

        updated = UserProfile(
            telegram_id=base.telegram_id,
            name=base.name,
            skills=skills,
            industries=industries,
            risk_appetite=risk,
            weekly_hours=hours,
            preferred_stack=stack,
            created_at=base.created_at,
        )
        return updated, notes

    async def _persist(self, profile: UserProfile) -> None:
        await self._repo.save_or_update_profile(
            profile.telegram_id,
            name=profile.name or "",
            skills_json=profile.skills,
            industries_json=profile.industries,
            risk_appetite=profile.risk_appetite,
            weekly_hours=profile.weekly_hours,
            preferred_stack_json=profile.preferred_stack,
        )

    async def update_from_conversation(
        self,
        telegram_id: int,
        idea_summary: IdeaSummary,
        analysis: AnalysisResult | None = None,
    ) -> UserProfile:
        profile = await self.get_or_create_profile(telegram_id)
        user_msg = (
            "Idee und Kontext:\n"
            f"{_idea_payload(idea_summary)}\n\n"
            "Analyse (Auszug):\n"
            f"{_analysis_excerpt(analysis)}"
        )
        try:
            data = await self._llm.complete_structured(
                _CONVERSATION_SYSTEM,
                user_msg,
                task="summarize",
            )
        except (LLMError, LLMResponseParsingError) as exc:
            logger.warning("Profile update LLM failed: %s", exc)
            return profile

        if not isinstance(data, dict):
            return profile

        updated, _ = self._apply_structured_updates(profile, data, include_notes=False)
        try:
            await self._persist(updated)
        except Exception:
            logger.exception("Failed to save profile after conversation update")
            return profile
        return updated

    async def format_profile_text(self, profile: UserProfile) -> str:
        risk_de = {
            "conservative": "vorsichtig",
            "moderate": "ausgewogen",
            "aggressive": "risikofreudig",
        }.get(profile.risk_appetite, profile.risk_appetite)

        wh = (
            f"{profile.weekly_hours:g}"
            if profile.weekly_hours and profile.weekly_hours > 0
            else "—"
        )

        lines = [
            "👤 Dein Gründer-Profil",
            "",
            f"Name: {profile.name or '—'}",
            f"Risiko-Bereitschaft: {risk_de}",
            f"Wochenstunden (geschätzt): {wh}",
            "",
            "Skills:",
        ]
        if profile.skills:
            for s in profile.skills:
                lines.append(f"  • {s}")
        else:
            lines.append("  — noch keine Einträge —")

        lines.extend(["", "Branchen / Erfahrung:"])
        if profile.industries:
            for s in profile.industries:
                lines.append(f"  • {s}")
        else:
            lines.append("  — noch keine Einträge —")

        lines.extend(["", "Bevorzugter Stack / Tools:"])
        if profile.preferred_stack:
            for s in profile.preferred_stack:
                lines.append(f"  • {s}")
        else:
            lines.append("  — noch keine Einträge —")

        return "\n".join(lines)

    async def interactive_profile_update(
        self, telegram_id: int, user_message: str
    ) -> tuple[str, UserProfile]:
        profile = await self.get_or_create_profile(telegram_id)
        try:
            data = await self._llm.complete_structured(
                _EXTRACTION_SYSTEM,
                user_message.strip(),
                task="summarize",
            )
        except (LLMError, LLMResponseParsingError) as exc:
            logger.warning("interactive_profile_update LLM failed: %s", exc)
            return (
                "Profil konnte gerade nicht verarbeitet werden. Bitte versuche es später erneut.",
                profile,
            )

        if not isinstance(data, dict):
            return ("Keine strukturierten Daten erkannt.", profile)

        updated, notes = self._apply_structured_updates(profile, data, include_notes=True)
        try:
            await self._persist(updated)
        except Exception:
            logger.exception("Failed to persist profile after interactive update")
            return (
                "Speichern ist fehlgeschlagen. Bitte versuche es erneut.",
                profile,
            )

        if notes:
            confirmation = f"✅ Aktualisiert: {notes}"
        else:
            confirmation = "✅ Profil wurde mit deinen Angaben abgeglichen und gespeichert."
        return (confirmation, updated)


def idea_summary_from_idea_row(idea: dict[str, Any]) -> IdeaSummary:
    """Build IdeaSummary from a DB `ideas` row (for batch learning)."""
    raw = BrainstormAnswers(
        raw_idea=idea.get("raw_idea") or "",
        persona=idea.get("persona") or "",
        current_solution=idea.get("current_solution") or "",
        switch_trigger=idea.get("switch_trigger") or "",
        monetization=idea.get("monetization") or "",
        distribution=idea.get("distribution") or "",
    )
    return IdeaSummary(
        problem_statement=idea.get("problem_statement") or "",
        target_audience=idea.get("target_audience") or "",
        solution=idea.get("solution") or "",
        monetization=idea.get("monetization") or "",
        distribution_channel=idea.get("distribution") or "",
        unfair_advantage=idea.get("unfair_advantage") or "",
        raw_answers=raw,
    )
