"""
Muster-Erkennung ueber mehrere Ideen eines Nutzers — LLM-gestuetzt.
"""

from __future__ import annotations

import logging
from typing import Any

from db.repository import Repository
from llm.client import LLMClient
from shared.exceptions import LLMError, LLMResponseParsingError

logger = logging.getLogger(__name__)

PATTERN_ANALYSIS_SYSTEM = """
Du analysierst mehrere validierte Geschaeftsideen desselben Nutzers und erkennst Muster.
Antworte NUR mit einem JSON-Objekt (kein Markdown), exakt mit diesen Keys:
- "themes": Array von kurzen Strings — gemeinsame Themen, Branchen, Problemklassen
- "preferred_business_models": Array — wiederkehrende Monetarisierungs-/Geschaeftsmodelle
- "audience_patterns": Array — wiederkehrende Zielgruppen oder Annahmen
- "strengths": Array — was bei mehreren Ideen stark wirkt (klare Staerken)
- "blind_spots": Array — was wiederholt schwach, unklar oder riskant wirkt
- "recommendation": ein String — konkrete Empfehlung, welche Art Idee als naechstes sinnvoll waere

Alle Texte auf Deutsch, sachlich, ohne Floskeln.
""".strip()

IDEA_COMPARE_SYSTEM = """
Du vergleichst zwei Geschaeftsideen desselben Nutzers kurz und klar auf Deutsch.
Struktur: Aehnlichkeiten, Unterschiede, welche Idee insgesamt ueberzeugender wirkt und warum.
Max. ~250 Woerter, keine Einleitungsphrasen.
""".strip()


class PatternRecognition:
    def __init__(self, llm: LLMClient, repo: Repository) -> None:
        self._llm = llm
        self._repo = repo

    def _idea_summary_block(self, row: dict[str, Any]) -> str:
        pid = row.get("id")
        prob = row.get("problem_statement") or row.get("raw_idea") or ""
        sol = row.get("solution") or ""
        aud = row.get("target_audience") or ""
        stat = row.get("status") or ""
        mon = row.get("monetization") or ""
        dist = row.get("distribution") or ""
        return (
            f"--- Idee id={pid} status={stat} ---\n"
            f"Problem: {prob}\n"
            f"Loesung: {sol}\n"
            f"Zielgruppe: {aud}\n"
            f"Monetarisierung: {mon}\n"
            f"Distribution: {dist}\n"
        )

    async def analyze_user_patterns(self, telegram_chat_id: int) -> dict[str, Any]:
        ideas = await self._repo.get_ideas_by_chat(telegram_chat_id, limit=20)
        if len(ideas) < 2:
            return {
                "enough_data": False,
                "message": "Zu wenige Ideen fuer Muster-Erkennung (mind. 2 noetig)",
            }

        blocks = [self._idea_summary_block(dict(r)) for r in ideas]
        user_message = "Ideen des Nutzers (chronologisch, neueste zuerst):\n\n" + "\n".join(
            blocks
        )

        try:
            raw = await self._llm.complete_structured(
                system_prompt=PATTERN_ANALYSIS_SYSTEM,
                user_message=user_message,
                task="analysis",
            )
        except (LLMError, LLMResponseParsingError) as exc:
            logger.error("Pattern analysis LLM failed: %s", exc)
            return {
                "enough_data": False,
                "message": "Muster-Analyse konnte nicht geladen werden. Bitte spaeter erneut versuchen.",
            }
        except Exception:
            logger.exception("Pattern analysis failed")
            return {
                "enough_data": False,
                "message": "Muster-Analyse konnte nicht geladen werden. Bitte spaeter erneut versuchen.",
            }

        if not isinstance(raw, dict):
            return {
                "enough_data": False,
                "message": "Unerwartete LLM-Antwort.",
            }

        def _as_str_list(key: str) -> list[str]:
            v = raw.get(key)
            if isinstance(v, list):
                return [str(x).strip() for x in v if str(x).strip()]
            if isinstance(v, str) and v.strip():
                return [v.strip()]
            return []

        themes = _as_str_list("themes")
        if not themes:
            themes = _as_str_list("common_themes")

        return {
            "enough_data": True,
            "themes": themes,
            "preferred_business_models": _as_str_list("preferred_business_models"),
            "audience_patterns": _as_str_list("audience_patterns"),
            "strengths": _as_str_list("strengths"),
            "blind_spots": _as_str_list("blind_spots"),
            "recommendation": str(raw.get("recommendation") or "").strip()
            or "(Keine Empfehlung generiert.)",
        }

    async def format_patterns_text(self, patterns: dict[str, Any]) -> str:
        if not patterns.get("enough_data"):
            msg = str(
                patterns.get("message")
                or "Nicht genug Daten fuer eine Muster-Analyse."
            )
            return f"📊 Muster\n\n{msg}"

        themes = patterns.get("themes") or []
        models = patterns.get("preferred_business_models") or []
        aud = patterns.get("audience_patterns") or []
        strengths = patterns.get("strengths") or []
        blind = patterns.get("blind_spots") or []
        rec = patterns.get("recommendation") or ""

        def bullet_lines(items: list[str]) -> str:
            return "\n".join(f"• {x}" for x in items) if items else "—"

        return (
            "📊 Deine Muster\n\n"
            f"🏷 Themen & Branchen\n{bullet_lines(themes)}\n\n"
            f"💰 Bevorzugte Geschaeftsmodelle\n{bullet_lines(models)}\n\n"
            f"👥 Zielgruppen-Muster\n{bullet_lines(aud)}\n\n"
            f"💪 Staerken\n{bullet_lines(strengths)}\n\n"
            f"⚠️ Blind spots\n{bullet_lines(blind)}\n\n"
            f"🎯 Empfehlung\n{rec}"
        )

    async def compare_ideas(self, idea_id_a: int, idea_id_b: int) -> str:
        a = await self._repo.get_idea(idea_id_a)
        b = await self._repo.get_idea(idea_id_b)
        if not a or not b:
            return "❌ Eine oder beide Ideen wurden nicht gefunden."
        if a.get("telegram_chat_id") != b.get("telegram_chat_id"):
            return "❌ Die Ideen gehoeren nicht zum gleichen Chat — Vergleich nicht moeglich."

        user_message = (
            f"IDEA A (id={idea_id_a}):\n{self._idea_summary_block(a)}\n\n"
            f"IDEA B (id={idea_id_b}):\n{self._idea_summary_block(b)}"
        )
        try:
            text = await self._llm.complete(
                system_prompt=IDEA_COMPARE_SYSTEM,
                user_message=user_message,
                task="analysis",
            )
        except (LLMError, LLMResponseParsingError) as exc:
            logger.error("Idea compare LLM failed: %s", exc)
            return "Vergleich konnte nicht erzeugt werden. Bitte spaeter erneut versuchen."
        except Exception:
            logger.exception("Idea compare failed")
            return "Vergleich konnte nicht erzeugt werden."

        return f"⚖️ Ideen-Vergleich\n\n{text.strip()}"


__all__ = ["PatternRecognition"]
