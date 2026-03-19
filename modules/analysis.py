"""
Analysis orchestration — 7-category scoring, devil's advocate, out-of-the-box ideas (LLM).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from llm.client import LLMClient
from llm.prompts.analysis import (
    DEVILS_ADVOCATE_SYSTEM_PROMPT,
    OUT_OF_BOX_SYSTEM_PROMPT,
    SCORING_SYSTEM_PROMPT,
)
from shared.constants import PROGRESS_MESSAGES, SCORING_CATEGORIES, SCORING_CATEGORY_LABELS
from shared.exceptions import LLMError, LLMResponseParsingError
from shared.types import (
    AnalysisResult,
    CategoryScore,
    CitedStatement,
    ConfidenceLevel,
    DevilsAdvocateResult,
    IdeaSummary,
    OutOfBoxIdea,
    ProgressCallback,
    Recommendation,
    ResearchBundle,
    ResearchResult,
    ScoreLevel,
    Source,
    TrendRadarResult,
)

logger = logging.getLogger(__name__)


class AnalysisModule:
    """Runs scoring, devil's advocate, and creative pivots over a research bundle."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def _notify(self, progress: ProgressCallback | None, message_key: str) -> None:
        if progress is None:
            return
        text = PROGRESS_MESSAGES.get(message_key, message_key)
        await progress(text)

    def _build_idea_block(self, summary: IdeaSummary) -> str:
        return (
            "## Idee (Zusammenfassung)\n"
            f"problem_statement:\n{summary.problem_statement}\n\n"
            f"target_audience:\n{summary.target_audience}\n\n"
            f"solution:\n{summary.solution}\n\n"
            f"monetization:\n{summary.monetization}\n\n"
            f"distribution_channel:\n{summary.distribution_channel}\n\n"
            f"unfair_advantage:\n{summary.unfair_advantage}\n"
        )

    def _format_statement(self, stmt: CitedStatement) -> list[str]:
        lines: list[str] = []
        st = stmt.statement_type.value if hasattr(stmt.statement_type, "value") else str(stmt.statement_type)
        conf = stmt.confidence.value if isinstance(stmt.confidence, ConfidenceLevel) else str(stmt.confidence)
        cat = f" [{stmt.category}]" if stmt.category else ""
        lines.append(f"- [{st}] ({conf}){cat} {stmt.text}")
        for src in stmt.sources[:3]:
            lines.append(f"  Quelle: {src.name} — {src.url}")
        return lines

    def _format_trend_radar(self, tr: TrendRadarResult) -> list[str]:
        lines: list[str] = [
            "## Trend-Radar",
            f"Verdict: {tr.verdict.value}",
        ]
        if tr.verdict_reasoning:
            lines.append(f"Begruendung: {tr.verdict_reasoning}")
        if tr.signals:
            for sig in tr.signals:
                if not sig.available:
                    err = sig.error_message or "nicht verfuegbar"
                    lines.append(f"- Signal {sig.source}: uebersprungen ({err})")
                    continue
                pv = ""
                if sig.periods and sig.values:
                    pv = f" letzte Werte: {list(zip(sig.periods[-4:], sig.values[-4:]))}"
                lines.append(f"- {sig.source}:{pv}")
        else:
            lines.append("(Keine Signalreihen)")
        if tr.sources:
            lines.append("Trend-Quellen:")
            for src in tr.sources[:6]:
                lines.append(f"  - {src.name}: {src.url}")
        return lines

    def _format_research_result(self, res: ResearchResult) -> list[str]:
        lines: list[str] = []
        if not res.success:
            err = res.error_message or "unbekannter Fehler"
            lines.append(f"## {res.tool_name} — FEHLGESCHLAGEN ({err})")
            return lines
        lines.append(f"## {res.tool_name}")
        if not res.statements:
            lines.append("(Keine Aussagen extrahiert)")
            return lines
        for stmt in res.statements:
            lines.extend(self._format_statement(stmt))
        return lines

    def _build_research_context(self, research: ResearchBundle) -> str:
        parts: list[str] = [
            f"idea_id: {research.idea_id}",
            f"Geschaetzte Quellen gesamt: {research.total_sources}",
            "",
        ]
        parts.extend(self._format_trend_radar(research.trend_radar))
        parts.append("")

        for res in research.results:
            if res.tool_name in ("research_meta", "research_orchestration"):
                continue
            parts.extend(self._format_research_result(res))
            parts.append("")

        text = "\n".join(parts).strip()
        if len(text) > 3000:
            text = text[:2970] + "\n\n[... gekuerzt wegen Zeichenlimit ~3000]"
        return text

    def _build_scores_context(self, scores: list[CategoryScore]) -> str:
        lines: list[str] = ["## Bewertung (Kategorien)"]
        by_cat = {s.category: s for s in scores}
        for cat in SCORING_CATEGORIES:
            s = by_cat.get(cat)
            label = SCORING_CATEGORY_LABELS.get(cat, cat)
            if s is None:
                lines.append(f"- {label} ({cat}): — keine Daten")
                continue
            lv = s.level.value if isinstance(s.level, ScoreLevel) else str(s.level)
            lines.append(f"- {label} ({cat}): {lv} — {s.reasoning}")
        return "\n".join(lines)

    def _parse_level(self, raw: Any) -> ScoreLevel:
        if raw is None:
            return ScoreLevel.INSUFFICIENT_DATA
        key = str(raw).lower().strip()
        mapping: dict[str, ScoreLevel] = {
            "strong": ScoreLevel.STRONG,
            "medium": ScoreLevel.MEDIUM,
            "weak": ScoreLevel.WEAK,
            "critical": ScoreLevel.CRITICAL,
            "insufficient_data": ScoreLevel.INSUFFICIENT_DATA,
        }
        return mapping.get(key, ScoreLevel.INSUFFICIENT_DATA)

    def _parse_recommendation(self, raw: Any) -> Recommendation:
        if raw is None:
            return Recommendation.NO_GO
        key = str(raw).lower().strip()
        mapping: dict[str, Recommendation] = {
            "go": Recommendation.GO,
            "conditional_go": Recommendation.CONDITIONAL_GO,
            "pivot": Recommendation.PIVOT,
            "no_go": Recommendation.NO_GO,
        }
        return mapping.get(key, Recommendation.NO_GO)

    def _category_score_from_dict(self, category: str, item: dict[str, Any]) -> CategoryScore:
        reasoning = str(item.get("reasoning") or "").strip()
        if not reasoning:
            reasoning = "Keine Begruendung geliefert."
        level = self._parse_level(item.get("level"))
        key_sources: list[Source] = []
        raw_src = item.get("key_sources")
        if isinstance(raw_src, list):
            for s in raw_src:
                if not isinstance(s, dict):
                    continue
                url = str(s.get("url") or "").strip()
                name = str(s.get("name") or "Quelle").strip()
                snippet = str(s.get("snippet") or "").strip()
                if url:
                    key_sources.append(
                        Source(
                            url=url,
                            name=name,
                            snippet=snippet,
                            source_type=str(s.get("source_type") or ""),
                            extra=s.get("extra") if isinstance(s.get("extra"), dict) else {},
                        )
                    )
        return CategoryScore(
            category=category,
            level=level,
            reasoning=reasoning,
            key_sources=key_sources,
        )

    def _default_insufficient_scores(self) -> list[CategoryScore]:
        msg = (
            "Automatische Bewertung war nicht moeglich (LLM-Fehler oder ungueltige Antwort). "
            "Bitte Analyse wiederholen; Recherchegrundlage ggf. pruefen."
        )
        return [
            CategoryScore(
                category=cat,
                level=ScoreLevel.INSUFFICIENT_DATA,
                reasoning=msg,
            )
            for cat in SCORING_CATEGORIES
        ]

    def _parse_scoring_payload(
        self,
        data: object,
    ) -> tuple[list[CategoryScore], Recommendation, str, str]:
        if not isinstance(data, dict):
            raise LLMResponseParsingError("Scoring: Antwort ist kein JSON-Objekt.")
        raw_scores = data.get("scores")
        if not isinstance(raw_scores, list):
            raise LLMResponseParsingError("Scoring: Feld 'scores' fehlt oder ist kein Array.")

        by_cat: dict[str, dict[str, Any]] = {}
        for item in raw_scores:
            if not isinstance(item, dict):
                continue
            cat = item.get("category")
            if isinstance(cat, str) and cat.strip() in SCORING_CATEGORIES:
                by_cat[cat.strip()] = item

        scores: list[CategoryScore] = []
        for cat in SCORING_CATEGORIES:
            item = by_cat.get(cat)
            if item is None:
                scores.append(
                    CategoryScore(
                        category=cat,
                        level=ScoreLevel.INSUFFICIENT_DATA,
                        reasoning=(
                            "Fuer diese Kategorie lieferte das Modell keinen Eintrag — "
                            "Datenlage unklar oder Parsing-Luecke."
                        ),
                    )
                )
            else:
                scores.append(self._category_score_from_dict(cat, item))

        rec = self._parse_recommendation(data.get("recommendation"))
        rec_reas = str(data.get("recommendation_reasoning") or "").strip()
        next_step = str(data.get("next_step") or "").strip()
        if not rec_reas:
            rec_reas = "Keine gesonderte Empfehlungsbegruendung geliefert."
        if not next_step:
            next_step = "Konkreten naechsten Validierungsschritt mit Team festlegen und Rechercheluecken schliessen."
        return scores, rec, rec_reas, next_step

    def _parse_devils_payload(self, data: object) -> DevilsAdvocateResult:
        if not isinstance(data, dict):
            return DevilsAdvocateResult()
        return DevilsAdvocateResult(
            kill_reason=str(data.get("kill_reason") or "").strip(),
            riskiest_assumption=str(data.get("riskiest_assumption") or "").strip(),
            must_be_true=str(data.get("must_be_true") or "").strip(),
            cheapest_test=str(data.get("cheapest_test") or "").strip(),
        )

    def _parse_out_of_box_payload(self, data: object) -> list[OutOfBoxIdea]:
        items: list[Any]
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            raw = data.get("ideas") or data.get("items") or data.get("pivots") or data.get("out_of_box")
            if isinstance(raw, list):
                items = raw
            elif isinstance(data.get("idea"), str):
                items = [data]
            else:
                items = []
        else:
            items = []

        out: list[OutOfBoxIdea] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            idea = str(it.get("idea") or "").strip()
            reasoning = str(it.get("reasoning") or "").strip()
            if idea and reasoning:
                out.append(OutOfBoxIdea(idea=idea, reasoning=reasoning))
            if len(out) >= 3:
                break
        return out

    async def _run_scoring(
        self,
        idea_block: str,
        research_ctx: str,
    ) -> tuple[list[CategoryScore], Recommendation, str, str]:
        user_message = f"{idea_block}\n\n## Recherche-Kontext\n{research_ctx}"
        data = await self._llm.complete_structured(
            system_prompt=SCORING_SYSTEM_PROMPT,
            user_message=user_message,
            task="analysis",
        )
        return self._parse_scoring_payload(data)

    async def _run_devils_advocate(
        self,
        idea_block: str,
        research_ctx: str,
        scores_ctx: str,
    ) -> DevilsAdvocateResult:
        user_message = (
            f"{idea_block}\n\n{scores_ctx}\n\n## Recherche-Kontext\n{research_ctx}"
        )
        data = await self._llm.complete_structured(
            system_prompt=DEVILS_ADVOCATE_SYSTEM_PROMPT,
            user_message=user_message,
            task="devils_advocate",
        )
        return self._parse_devils_payload(data)

    async def _run_out_of_box(self, idea_block: str, research_ctx: str) -> list[OutOfBoxIdea]:
        user_message = f"{idea_block}\n\n## Recherche-Kontext\n{research_ctx}"
        data = await self._llm.complete_structured(
            system_prompt=OUT_OF_BOX_SYSTEM_PROMPT,
            user_message=user_message,
            task="out_of_box",
        )
        return self._parse_out_of_box_payload(data)

    async def run(
        self,
        idea_id: int,
        summary: IdeaSummary,
        research: ResearchBundle,
        progress: ProgressCallback | None = None,
    ) -> AnalysisResult:
        research_ctx = self._build_research_context(research)
        idea_block = self._build_idea_block(summary)

        await self._notify(progress, "analysis")

        scores: list[CategoryScore]
        rec: Recommendation
        rec_reas: str
        next_step: str

        try:
            scores, rec, rec_reas, next_step = await self._run_scoring(idea_block, research_ctx)
        except asyncio.CancelledError:
            raise
        except (LLMError, LLMResponseParsingError) as exc:
            logger.error("Analysis scoring LLM failed idea_id=%s: %s", idea_id, exc)
            scores = self._default_insufficient_scores()
            rec = Recommendation.NO_GO
            rec_reas = (
                "Die automatische Bewertung konnte nicht abgeschlossen werden. "
                "Bitte erneut versuchen, wenn die API wieder verfuegbar ist."
            )
            next_step = "Analyse nach einem kurzen Warten wiederholen; bei anhaltendem Fehler Logs pruefen."
        except Exception:
            logger.exception("Analysis scoring failed idea_id=%s", idea_id)
            scores = self._default_insufficient_scores()
            rec = Recommendation.NO_GO
            rec_reas = (
                "Die automatische Bewertung konnte nicht abgeschlossen werden (unerwarteter Fehler)."
            )
            next_step = "Analyse erneut starten und technische Konfiguration pruefen."

        await self._notify(progress, "devils_advocate")
        scores_ctx = self._build_scores_context(scores)

        async def safe_devils() -> DevilsAdvocateResult:
            try:
                return await self._run_devils_advocate(idea_block, research_ctx, scores_ctx)
            except asyncio.CancelledError:
                raise
            except (LLMError, LLMResponseParsingError) as exc:
                logger.error("Devils advocate LLM failed idea_id=%s: %s", idea_id, exc)
                return DevilsAdvocateResult()
            except Exception:
                logger.exception("Devils advocate failed idea_id=%s", idea_id)
                return DevilsAdvocateResult()

        async def safe_oob() -> list[OutOfBoxIdea]:
            try:
                return await self._run_out_of_box(idea_block, research_ctx)
            except asyncio.CancelledError:
                raise
            except (LLMError, LLMResponseParsingError) as exc:
                logger.error("Out-of-box LLM failed idea_id=%s: %s", idea_id, exc)
                return []
            except Exception:
                logger.exception("Out-of-box failed idea_id=%s", idea_id)
                return []

        devils, oob = await asyncio.gather(safe_devils(), safe_oob())

        return AnalysisResult(
            idea_id=idea_id,
            scores=scores,
            recommendation=rec,
            recommendation_reasoning=rec_reas,
            next_step=next_step,
            out_of_box_ideas=oob,
            devils_advocate=devils,
        )
