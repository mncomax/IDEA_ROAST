"""
Report module — Telegram text + Markdown export for validation results.

Telegram messages are template-rendered (no LLM) for speed and reliability.
The LLM client and REPORT_SYSTEM_PROMPT are available for optional narrative flows.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from os import makedirs
from pathlib import Path

from llm.client import LLMClient
from llm.prompts.report import REPORT_SYSTEM_PROMPT
from shared.constants import (
    EXPORT_FILENAME_TEMPLATE,
    SCORING_CATEGORY_LABELS,
    SCORING_CATEGORIES,
    TELEGRAM_MAX_MESSAGE_LENGTH,
)
from shared.types import (
    AnalysisResult,
    CategoryScore,
    CitedStatement,
    DevilsAdvocateResult,
    IdeaSummary,
    OutOfBoxIdea,
    ProgressCallback,  # noqa: F401 — contract / future progress hooks
    Recommendation,
    ResearchBundle,
    ResearchResult,
    ScoreLevel,
    Source,
    TrendRadarResult,
    TrendVerdict,
    ValidationReport,
)

logger = logging.getLogger(__name__)

_TREND_VERDICT_DE: dict[TrendVerdict, str] = {
    TrendVerdict.RISING: "Steigend",
    TrendVerdict.PLATEAU: "Plateau",
    TrendVerdict.DECLINING: "Fallend",
    TrendVerdict.EARLY: "Frueh / fruehe Phase",
    TrendVerdict.HYPE_PEAK: "Hype-Peak",
    TrendVerdict.INSUFFICIENT_DATA: "Unzureichende Daten",
}

_RECOMMENDATION_DE: dict[Recommendation, str] = {
    Recommendation.GO: "Go",
    Recommendation.CONDITIONAL_GO: "Bedingtes Go",
    Recommendation.PIVOT: "Pivot",
    Recommendation.NO_GO: "No-Go",
}

_EXPORT_DIR = Path("/tmp/idearoast_exports")


class ReportModule:
    """Build Telegram-ready text and Markdown exports from analysis + research."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        logger.debug("ReportModule init, REPORT_SYSTEM_PROMPT length=%d", len(REPORT_SYSTEM_PROMPT))

    @staticmethod
    def _score_emoji(level: ScoreLevel) -> str:
        return {
            ScoreLevel.STRONG: "🟢",
            ScoreLevel.MEDIUM: "🟡",
            ScoreLevel.WEAK: "🟠",
            ScoreLevel.CRITICAL: "🔴",
            ScoreLevel.INSUFFICIENT_DATA: "⚪",
        }[level]

    @staticmethod
    def _recommendation_emoji(rec: Recommendation) -> str:
        return {
            Recommendation.GO: "🟢",
            Recommendation.CONDITIONAL_GO: "🟡",
            Recommendation.PIVOT: "🟠",
            Recommendation.NO_GO: "🔴",
        }[rec]

    @staticmethod
    def _sanitize_filename(text: str) -> str:
        raw = (text or "").strip().lower()
        raw = re.sub(r"\s+", "_", raw)
        raw = re.sub(r"[^a-z0-9._-]", "", raw)
        raw = raw.strip("._-") or "idee"
        return raw[:30]

    @staticmethod
    def _scores_by_category(analysis: AnalysisResult) -> dict[str, CategoryScore]:
        return {s.category: s for s in analysis.scores}

    def _format_score_lines(self, analysis: AnalysisResult) -> list[str]:
        by_cat = self._scores_by_category(analysis)
        lines: list[str] = []
        for cat in SCORING_CATEGORIES:
            label = SCORING_CATEGORY_LABELS.get(cat, cat)
            sc = by_cat.get(cat)
            if sc is None:
                lines.append(f"⚪ {label}: (kein Score)")
                continue
            em = self._score_emoji(sc.level)
            reason = (sc.reasoning or "").strip().replace("\n", " ")
            lines.append(f"{em} {label}: {reason}")
        for cat, sc in by_cat.items():
            if cat not in SCORING_CATEGORIES:
                label = SCORING_CATEGORY_LABELS.get(cat, cat)
                em = self._score_emoji(sc.level)
                reason = (sc.reasoning or "").strip().replace("\n", " ")
                lines.append(f"{em} {label}: {reason}")
        return lines

    @staticmethod
    def _trend_block(trend: TrendRadarResult) -> str:
        verdict = _TREND_VERDICT_DE.get(trend.verdict, trend.verdict.value)
        reasoning = (trend.verdict_reasoning or "").strip()
        if reasoning:
            return f"{verdict}\n{reasoning}"
        return verdict

    @staticmethod
    def _devils_block(d: DevilsAdvocateResult) -> tuple[str, str]:
        kill = (d.kill_reason or "").strip()
        test = (d.cheapest_test or "").strip()
        return kill, test

    @staticmethod
    def _out_of_box_lines(ideas: list[OutOfBoxIdea], include_reasoning: bool) -> list[str]:
        lines: list[str] = []
        for i, ob in enumerate(ideas, start=1):
            idea = (ob.idea or "").strip()
            if not idea:
                continue
            if include_reasoning and (ob.reasoning or "").strip():
                lines.append(f"{i}. {idea}\n   → {(ob.reasoning or '').strip()}")
            else:
                lines.append(f"{i}. {idea}")
        return lines

    def _build_telegram_body(
        self,
        summary: IdeaSummary,
        research: ResearchBundle,
        analysis: AnalysisResult,
        *,
        out_of_box_mode: str = "full",
    ) -> str:
        rec_emoji = self._recommendation_emoji(analysis.recommendation)
        rec_de = _RECOMMENDATION_DE.get(analysis.recommendation, analysis.recommendation.value)
        verdict_line = f"VERDICT: {rec_de}"
        reasoning = (analysis.recommendation_reasoning or "").strip()

        parts: list[str] = [
            f"{rec_emoji} Idea Roast — Validierung",
            "",
            verdict_line,
        ]
        if reasoning:
            parts.append(reasoning)

        parts.extend(["", "📊 Scores"])
        parts.extend(self._format_score_lines(analysis))

        parts.extend(["", "📈 Trend"])
        parts.append(self._trend_block(research.trend_radar))

        da = analysis.devils_advocate
        kill, cheapest = self._devils_block(da)
        parts.extend(["", "😈 Devils Advocate"])
        if kill:
            parts.append(f"Kill-Argument: {kill}")
        if cheapest:
            parts.append(f"Billigster Test: {cheapest}")

        parts.extend(["", "💡 Out-of-the-Box"])
        if out_of_box_mode == "minimal":
            parts.append("(Gekürzt — vollständig im Markdown-Export.)")
        else:
            include_reasoning = out_of_box_mode == "full"
            ob_lines = self._out_of_box_lines(
                analysis.out_of_box_ideas,
                include_reasoning=include_reasoning,
            )
            if ob_lines:
                parts.extend(ob_lines)
            else:
                parts.append("(keine)")

        next_step = (analysis.next_step or "").strip()
        parts.extend(["", "➡️ Nächster Schritt"])
        parts.append(next_step if next_step else "(nicht gesetzt)")

        parts.extend(["", "—", f"Idee: {(summary.problem_statement or '').strip()[:200]}"])

        return "\n".join(parts)

    def _truncate_telegram(self, text: str) -> str:
        if len(text) <= TELEGRAM_MAX_MESSAGE_LENGTH:
            return text
        return text[: TELEGRAM_MAX_MESSAGE_LENGTH - 1] + "…"

    async def generate_telegram_report(
        self,
        summary: IdeaSummary,
        research: ResearchBundle,
        analysis: AnalysisResult,
    ) -> str:
        candidates = (
            self._build_telegram_body(summary, research, analysis, out_of_box_mode="full"),
            self._build_telegram_body(summary, research, analysis, out_of_box_mode="compact"),
            self._build_telegram_body(summary, research, analysis, out_of_box_mode="minimal"),
        )
        for body in candidates:
            if len(body) <= TELEGRAM_MAX_MESSAGE_LENGTH:
                return body
        return self._truncate_telegram(candidates[-1])

    @staticmethod
    def _escape_md(text: str) -> str:
        return text.replace("\\", "\\\\").replace("|", "\\|")

    @staticmethod
    def _collect_statement_sources(results: list[ResearchResult]) -> list[tuple[CitedStatement, Source]]:
        out: list[tuple[CitedStatement, Source]] = []
        for rr in results:
            for stmt in rr.statements:
                for src in stmt.sources:
                    out.append((stmt, src))
        return out

    @staticmethod
    def _unique_sources_by_url(pairs: list[tuple[CitedStatement, Source]]) -> list[Source]:
        seen: set[str] = set()
        ordered: list[Source] = []
        for _, src in pairs:
            url = (src.url or "").strip()
            key = url or f"{src.name}:{src.snippet[:40]}"
            if key in seen:
                continue
            seen.add(key)
            ordered.append(src)
        return ordered

    def _markdown_research_sources(self, research: ResearchBundle) -> str:
        pairs = self._collect_statement_sources(research.results)
        uniq = self._unique_sources_by_url(pairs)
        lines: list[str] = ["## Recherche-Quellen (Statements)", ""]
        if not uniq:
            lines.append("_Keine Quellen erfasst._")
            return "\n".join(lines)
        for i, s in enumerate(uniq, start=1):
            name = self._escape_md(s.name or "(ohne Name)")
            url = self._escape_md(s.url or "")
            snip = (s.snippet or "").strip().replace("\n", " ")
            lines.append(f"{i}. **{name}** — {url}")
            lines.append(f"   - Snippet: {snip}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def _markdown_citation_appendix(self, research: ResearchBundle) -> str:
        lines: list[str] = ["## Anhang: Zitate & Belege", ""]
        any_stmt = False
        for rr in research.results:
            tool = self._escape_md(rr.tool_name)
            lines.append(f"### Tool: `{tool}`")
            lines.append("")
            for stmt in rr.statements:
                any_stmt = True
                st = (stmt.text or "").strip()
                lines.append(f"- **Aussage:** {st}")
                lines.append(
                    f"  - Typ: `{stmt.statement_type.value}` | "
                    f"Confidence: `{stmt.confidence.value}` | "
                    f"Kategorie: `{stmt.category or '-'}`"
                )
                if stmt.sources:
                    for src in stmt.sources:
                        lines.append(
                            f"  - Quelle: {self._escape_md(src.name)} — {self._escape_md(src.url)}"
                        )
                lines.append("")
        if not any_stmt:
            lines.append("_Keine CitedStatements in den Research-Ergebnissen._")
        return "\n".join(lines).rstrip()

    def _markdown_scoring(self, analysis: AnalysisResult) -> str:
        lines: list[str] = ["## Scoring", ""]
        by_cat = self._scores_by_category(analysis)
        for cat in SCORING_CATEGORIES:
            label = SCORING_CATEGORY_LABELS.get(cat, cat)
            sc = by_cat.get(cat)
            if sc is None:
                lines.append(f"### {label}")
                lines.append("_Kein Score._")
                lines.append("")
                continue
            em = self._score_emoji(sc.level)
            lines.append(f"### {em} {label} (`{sc.level.value}`)")
            lines.append("")
            lines.append((sc.reasoning or "_Keine Begründung._").strip())
            lines.append("")
            if sc.key_sources:
                lines.append("**Key sources:**")
                for ks in sc.key_sources:
                    lines.append(
                        f"- {self._escape_md(ks.name)} — {self._escape_md(ks.url)}"
                    )
                lines.append("")
        return "\n".join(lines).rstrip()

    def _markdown_devils(self, d: DevilsAdvocateResult) -> str:
        parts = [
            "## Devils Advocate",
            "",
            f"**Kill-Argument:** {(d.kill_reason or '_—_').strip()}",
            "",
            f"**Riskanteste Annahme:** {(d.riskiest_assumption or '_—_').strip()}",
            "",
            f"**Muss wahr sein:** {(d.must_be_true or '_—_').strip()}",
            "",
            f"**Billigster Test:** {(d.cheapest_test or '_—_').strip()}",
        ]
        return "\n".join(parts)

    def _markdown_out_of_box(self, ideas: list[OutOfBoxIdea]) -> str:
        lines = ["## Out-of-the-Box-Ideen", ""]
        if not ideas:
            lines.append("_Keine._")
            return "\n".join(lines)
        for i, ob in enumerate(ideas, start=1):
            lines.append(f"### {i}. {(ob.idea or '').strip() or '(leer)'}")
            lines.append("")
            lines.append((ob.reasoning or "_—_").strip())
            lines.append("")
        return "\n".join(lines).rstrip()

    async def generate_markdown_export(
        self,
        idea_id: int,
        summary: IdeaSummary,
        research: ResearchBundle,
        analysis: AnalysisResult,
    ) -> str:
        now = datetime.utcnow()
        date_str = now.strftime("%Y-%m-%d %H:%M UTC")

        trend = research.trend_radar
        chart_ref = (trend.chart_image_path or "").strip()
        chart_line = (
            f"Gespeichert unter: `{self._escape_md(chart_ref)}`"
            if chart_ref
            else "_Kein Chart generiert._"
        )

        header = "\n".join(
            [
                f"# Idea Roast — Validierungsreport",
                "",
                f"- **Idea ID:** {idea_id}",
                f"- **Datum:** {date_str}",
                f"- **Empfehlung:** {_RECOMMENDATION_DE.get(analysis.recommendation, analysis.recommendation.value)}",
                "",
                f"## Idee (Kurzfassung)",
                "",
                f"**Problem:** {(summary.problem_statement or '').strip()}",
                "",
                f"**Zielgruppe:** {(summary.target_audience or '').strip()}",
                "",
                f"**Lösung:** {(summary.solution or '').strip()}",
                "",
                f"**Monetarisierung:** {(summary.monetization or '').strip()}",
                "",
                f"**Distribution:** {(summary.distribution_channel or '').strip()}",
                "",
                f"**Unfair Advantage:** {(summary.unfair_advantage or '').strip() or '—'}",
                "",
                "---",
                "",
            ]
        )

        verdict_section = "\n".join(
            [
                "## Urteil",
                "",
                f"**Begründung:** {(analysis.recommendation_reasoning or '_—_').strip()}",
                "",
                f"**Nächster Schritt:** {(analysis.next_step or '_—_').strip()}",
                "",
            ]
        )

        trend_src_lines: list[str] = []
        if trend.sources:
            trend_src_lines.append("**Trend-Quellen:**")
            for ts in trend.sources:
                trend_src_lines.append(
                    f"- {self._escape_md(ts.name)} — {self._escape_md(ts.url)}"
                )
            trend_src_lines.append("")

        trend_section = "\n".join(
            [
                "## Trend-Radar",
                "",
                f"**Urteil:** {_TREND_VERDICT_DE.get(trend.verdict, trend.verdict.value)}",
                "",
                f"**Begründung:** {(trend.verdict_reasoning or '_—_').strip()}",
                "",
                *trend_src_lines,
                "### Trend-Chart",
                "",
                chart_line,
                "",
            ]
        )

        body = "\n\n".join(
            [
                header.rstrip(),
                self._markdown_scoring(analysis),
                verdict_section.rstrip(),
                trend_section.rstrip(),
                self._markdown_devils(analysis.devils_advocate),
                self._markdown_out_of_box(analysis.out_of_box_ideas),
                self._markdown_research_sources(research),
                self._markdown_citation_appendix(research),
            ]
        )
        return body.strip() + "\n"

    async def export_to_file(
        self,
        idea_id: int,
        summary: IdeaSummary,
        research: ResearchBundle,
        analysis: AnalysisResult,
    ) -> str:
        md = await self.generate_markdown_export(idea_id, summary, research, analysis)
        idea_name = self._sanitize_filename(summary.problem_statement or "idee")
        date_part = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = EXPORT_FILENAME_TEMPLATE.format(idea_name=idea_name, date=date_part)
        makedirs(_EXPORT_DIR, exist_ok=True)
        path = _EXPORT_DIR / filename
        path.write_text(md, encoding="utf-8")
        logger.info("Markdown export written: %s", path)
        return str(path)

    async def create_full_report(
        self,
        idea_id: int,
        summary: IdeaSummary,
        research: ResearchBundle,
        analysis: AnalysisResult,
    ) -> ValidationReport:
        await self.generate_telegram_report(summary, research, analysis)
        export_path = await self.export_to_file(idea_id, summary, research, analysis)
        return ValidationReport(
            idea_id=idea_id,
            idea_summary=summary,
            research=research,
            analysis=analysis,
            generated_at=datetime.utcnow(),
            export_file_path=export_path,
        )
