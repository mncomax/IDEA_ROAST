"""
Deep-Dive: Quellen, Trend-Details, Report-Export und Folgefragen nach dem Validierungsbericht.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from shared.constants import TELEGRAM_MAX_MESSAGE_LENGTH
from shared.types import (
    AnalysisResult,
    ConversationContext,
    ResearchBundle,
    Source,
    ValidationReport,
)

logger = logging.getLogger(__name__)

# Callback-Daten müssen mit ^deep_ matchen
CB_SOURCES = "deep_sources"
CB_TREND = "deep_trend"
CB_EXPORT = "deep_export"
CB_QUESTION = "deep_question"

_CATEGORY_ORDER = ("market", "competition", "sentiment", "trend", "sonstige")
_CATEGORY_LABELS: dict[str, str] = {
    "market": "Markt",
    "competition": "Wettbewerb",
    "sentiment": "Stimmung / Sentiment",
    "trend": "Trend",
    "sonstige": "Sonstige",
}


def _normalize_category(raw: str) -> str:
    key = (raw or "").strip().lower()
    if key in _CATEGORY_LABELS:
        return key
    return "sonstige"


def _source_key(src: Source) -> str:
    u = (src.url or "").strip()
    if u:
        return u
    return f"{src.name}|{src.snippet[:40]}"


def build_report_keyboard() -> InlineKeyboardMarkup:
    """Inline-Tastatur nach dem Validierungsbericht."""
    row1 = [
        InlineKeyboardButton("📋 Quellen zeigen", callback_data=CB_SOURCES),
        InlineKeyboardButton("📊 Trend-Details", callback_data=CB_TREND),
    ]
    row2 = [
        InlineKeyboardButton("📄 Report als Datei", callback_data=CB_EXPORT),
        InlineKeyboardButton("❓ Frage stellen", callback_data=CB_QUESTION),
    ]
    return InlineKeyboardMarkup([row1, row2])


def _split_text_chunks(text: str, max_len: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= max_len:
            chunks.append(rest)
            break
        cut = rest.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            cut = max_len
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    return chunks


async def handle_deep_dive_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer()
    except Exception:
        logger.debug("callback_query.answer failed", exc_info=True)

    data = (q.data or "").strip()
    if data == CB_SOURCES:
        await _show_sources(update, context)
    elif data == CB_TREND:
        await _show_trend_details(update, context)
    elif data == CB_EXPORT:
        await _export_report(update, context)
    elif data == CB_QUESTION:
        await _handle_question(update, context)
    else:
        msg = q.message
        if msg:
            await msg.reply_text("Unbekannte Aktion. Bitte nutze die Buttons erneut.")


def _get_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[ConversationContext]:
    raw = context.user_data.get("conv_context")
    return raw if isinstance(raw, ConversationContext) else None


async def _reply_from_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs
) -> None:
    q = update.callback_query
    msg = q.message if q else None
    chat = update.effective_chat
    if msg:
        await msg.reply_text(text, **kwargs)
    elif chat:
        await context.bot.send_message(chat_id=chat.id, text=text, **kwargs)


async def _show_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conv = _get_conv(update, context)
    if not conv or not conv.research_bundle:
        await _reply_from_callback(
            update,
            context,
            "Noch keine Recherche-Daten vorhanden. Bitte zuerst /validate ausfuehren.",
        )
        return

    bundle = conv.research_bundle
    by_cat: dict[str, dict[str, Source]] = defaultdict(dict)

    for res in bundle.results:
        for stmt in res.statements:
            cat = _normalize_category(stmt.category)
            for src in stmt.sources:
                by_cat[cat][_source_key(src)] = src

    tr = bundle.trend_radar
    for src in tr.sources:
        by_cat["trend"][_source_key(src)] = src

    parts: list[str] = ["📚 Quellen (nach Kategorie)\n"]

    for cat in _CATEGORY_ORDER:
        src_map = by_cat.get(cat)
        if not src_map:
            continue
        label = _CATEGORY_LABELS.get(cat, cat)
        parts.append(f"\n— {label} —\n")
        for i, src in enumerate(src_map.values(), start=1):
            stype = (src.source_type or "unbekannt").strip()
            name = (src.name or "Ohne Titel").strip()
            url = (src.url or "").strip()
            snip = (src.snippet or "")[:100]
            line = f"{i}. [{stype}] {name}\n   URL: {url}\n   {snip}"
            parts.append(line + "\n")

    if len(parts) <= 1:
        await _reply_from_callback(
            update,
            context,
            "Keine einzelnen Quellen-Eintraege gefunden. Die Recherche lieferte evtl. nur Trend-Signale ohne URLs.",
        )
        return

    full = "".join(parts)
    for chunk in _split_text_chunks(full):
        await _reply_from_callback(update, context, chunk)


async def _show_trend_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conv = _get_conv(update, context)
    if not conv or not conv.research_bundle:
        await _reply_from_callback(
            update,
            context,
            "Kein Trend-Radar vorhanden. Bitte zuerst /validate ausfuehren.",
        )
        return

    tr = conv.research_bundle.trend_radar
    lines: list[str] = ["📊 Trend-Radar — Details\n"]

    if not tr.signals:
        lines.append("(Keine Einzelsignale gespeichert.)\n")
    else:
        for sig in tr.signals:
            status = "verfuegbar" if sig.available else "nicht verfuegbar"
            if sig.error_message and not sig.available:
                status += f" ({sig.error_message[:80]})"
            if sig.periods and sig.values:
                pairs = []
                for p, v in zip(sig.periods, sig.values):
                    pairs.append(f"{p}: {v:.1f}")
                val_summary = ", ".join(pairs[:12])
                if len(pairs) > 12:
                    val_summary += " …"
            elif sig.values:
                val_summary = ", ".join(f"{v:.1f}" for v in sig.values[:12])
            else:
                val_summary = "—"
            src_name = (sig.source or "Signal").strip()
            lines.append(f"• {src_name} — {status}\n  Werte: {val_summary}\n")

    verdict = tr.verdict.value.replace("_", " ")
    lines.append(f"\nUrteil: {verdict}\n")
    if tr.verdict_reasoning:
        lines.append(f"Begruendung:\n{tr.verdict_reasoning}\n")

    text = "".join(lines)
    for chunk in _split_text_chunks(text):
        await _reply_from_callback(update, context, chunk)

    path = (tr.chart_image_path or "").strip()
    if path and os.path.isfile(path):
        chat = update.effective_chat
        if not chat:
            return
        try:
            with open(path, "rb") as photo:
                await context.bot.send_photo(chat_id=chat.id, photo=photo, caption="Trend-Chart (Radar)")
        except Exception:
            logger.warning("Trend-Chart konnte nicht erneut gesendet werden", exc_info=True)
            await _reply_from_callback(
                update,
                context,
                "Chart-Datei war nicht lesbar. Pfad evtl. veraltet.",
            )


async def _export_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    path_str = context.user_data.get("last_export_path")
    report = context.user_data.get("last_validation_report")

    if isinstance(report, ValidationReport) and report.export_file_path and not path_str:
        path_str = report.export_file_path

    if not path_str or not isinstance(path_str, str):
        await _reply_from_callback(
            update,
            context,
            "Kein exportierter Report gefunden. Bitte fuehre zuerst eine Validierung aus (/validate), "
            "die den Markdown-Export erzeugt.",
        )
        return

    path = Path(path_str)
    if not path.is_file():
        await _reply_from_callback(
            update,
            context,
            f"Die Report-Datei fehlt auf dem Server: {path.name}\nBitte /validate erneut ausfuehren.",
        )
        return

    chat = update.effective_chat
    if not chat:
        return
    try:
        with open(path, "rb") as doc:
            await context.bot.send_document(
                chat_id=chat.id,
                document=doc,
                filename=path.name,
                caption="Validierungsbericht (Markdown)",
            )
    except Exception:
        logger.exception("send_document failed for export")
        await _reply_from_callback(
            update,
            context,
            "Die Datei konnte nicht gesendet werden. Bitte spaeter erneut versuchen.",
        )


async def _handle_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting_deep_dive_question"] = True
    await _reply_from_callback(
        update,
        context,
        "Stell jetzt deine Folgefrage als Freitext — ich antworte nur auf Basis der gespeicherten "
        "Forschungsdaten.\n\nZum Abbrechen: /help oder einen anderen Befehl.",
    )


def _format_research_for_llm(bundle: ResearchBundle, analysis: Optional[AnalysisResult]) -> str:
    lines: list[str] = ["=== RECHERCHE ===\n"]

    for res in bundle.results:
        lines.append(f"Tool: {res.tool_name} (ok={res.success})\n")
        for stmt in res.statements:
            lines.append(
                f"  [{stmt.category}] {stmt.text}\n"
                f"    confidence={stmt.confidence.value}, type={stmt.statement_type.value}\n"
            )
            for s in stmt.sources:
                lines.append(
                    f"    - {s.name} | {s.url}\n      {s.snippet[:400]}\n"
                )

    tr = bundle.trend_radar
    lines.append("\n=== TREND RADAR ===\n")
    lines.append(f"verdict={tr.verdict.value}, reasoning={tr.verdict_reasoning}\n")
    for sig in tr.signals:
        lines.append(
            f"  signal={sig.source} available={sig.available} "
            f"periods={sig.periods} values={sig.values} err={sig.error_message}\n"
        )

    if analysis:
        lines.append("\n=== ANALYSE ===\n")
        lines.append(f"recommendation={analysis.recommendation.value}\n")
        lines.append(f"reasoning={analysis.recommendation_reasoning}\n")
        for sc in analysis.scores:
            lines.append(
                f"  {sc.category}: {sc.level.value} — {sc.reasoning}\n"
            )
        if analysis.devils_advocate:
            da = analysis.devils_advocate
            lines.append(
                f"devils_advocate: kill={da.kill_reason}, risk={da.riskiest_assumption}\n"
            )

    text = "".join(lines)
    max_ctx = 14000
    if len(text) > max_ctx:
        return text[:max_ctx] + "\n[… gekuerzt …]"
    return text


async def handle_deep_dive_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Beantwortet eine Folgefrage, wenn awaiting_deep_dive_question gesetzt ist."""
    if not context.user_data.get("awaiting_deep_dive_question"):
        return
    if not update.message or not update.message.text:
        return

    question = update.message.text.strip()
    if not question:
        return

    conv = _get_conv(update, context)
    if not conv:
        context.user_data["awaiting_deep_dive_question"] = False
        await update.message.reply_text(
            "Kein Gespraechskontext. Bitte /idea und /validate erneut."
        )
        return

    if not conv.research_bundle:
        context.user_data["awaiting_deep_dive_question"] = False
        await update.message.reply_text(
            "Keine Recherche-Daten mehr im Speicher. Bitte erneut /validate ausfuehren."
        )
        return

    llm = context.bot_data.get("llm_client")
    if not llm:
        context.user_data["awaiting_deep_dive_question"] = False
        await update.message.reply_text("LLM nicht geladen — Folgefrage nicht moeglich.")
        return

    context_block = _format_research_for_llm(
        conv.research_bundle,
        conv.analysis_result,
    )
    system_prompt = (
        "Du beantwortest Folgefragen zu einer Ideen-Validierung. Nutze NUR die Forschungsdaten. "
        "Erfinde NICHTS. Deutsch, kurz."
    )
    user_payload = f"Kontext:\n{context_block}\n\nFrage:\n{question}"

    try:
        answer = await llm.complete(
            system_prompt=system_prompt,
            user_message=user_payload,
            task="source_query",
            max_tokens=2048,
        )
    except Exception as exc:
        logger.exception("source_query LLM failed")
        context.user_data["awaiting_deep_dive_question"] = False
        await update.message.reply_text(
            f"Die Antwort konnte nicht erzeugt werden: {exc!s}\nBitte spaeter erneut versuchen."
        )
        return

    context.user_data["awaiting_deep_dive_question"] = False

    reply = (answer or "").strip() or "(Leere Antwort vom Modell.)"
    for chunk in _split_text_chunks(reply):
        await update.message.reply_text(chunk)

    await update.message.reply_text(
        "Weitere Frage? Erneut «Frage stellen» druecken.",
        reply_markup=build_report_keyboard(),
    )


def get_deep_dive_handlers() -> list:
    return [
        CallbackQueryHandler(handle_deep_dive_callback, pattern="^deep_"),
    ]
