"""
Ideen-Verlauf, Detailansicht und Outcome-Erfassung (Telegram).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

HandlerCallback = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]

from shared.constants import SCORING_CATEGORY_LABELS, TELEGRAM_MAX_MESSAGE_LENGTH
from shared.types import AnalysisResult, ScoreLevel

logger = logging.getLogger(__name__)

# user_data: "outcome_notes_pending" -> {"idea_id": int, "outcome": str, "label_de": str}

_RECOMMENDATION_DE: dict[str, str] = {
    "go": "Go",
    "conditional_go": "Bedingtes Go",
    "pivot": "Pivot",
    "no_go": "No-Go",
}

_RECOMMENDATION_EMOJI: dict[str, str] = {
    "go": "🟢",
    "conditional_go": "🟡",
    "pivot": "🟠",
    "no_go": "🔴",
}

_SCORE_LEVEL_EMOJI: dict[str, str] = {
    ScoreLevel.STRONG.value: "🟢",
    ScoreLevel.MEDIUM.value: "🟡",
    ScoreLevel.WEAK.value: "🟠",
    ScoreLevel.CRITICAL.value: "🔴",
    ScoreLevel.INSUFFICIENT_DATA.value: "⚪",
}

_STATUS_EMOJI_PLAIN: dict[str, str] = {
    "brainstorm": "🧠",
    "validated": "✅",
    "archived": "📦",
}

_OUTCOME_LABELS_DE: dict[str, str] = {
    "built": "Gebaut & läuft",
    "pivoted": "Gepivoted",
    "paused": "Pausiert",
    "dropped": "Verworfen",
    "open": "Noch offen",
}

_RE_HISTORY_DETAIL = re.compile(r"^history_detail_(\d+)$")
_RE_HISTORY_OUTCOME = re.compile(r"^history_outcome_(\d+)$")
_RE_OUTCOME_PICK = re.compile(r"^outcome_(\d+)_(built|pivoted|paused|dropped|open)$")


def _split_chunks(text: str, max_len: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
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
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    return chunks


def build_validation_snapshot_dict(analysis: AnalysisResult) -> dict[str, Any]:
    """Kompakte, JSON-serialisierbare Analyse fuer research_cache."""
    return {
        "recommendation": analysis.recommendation.value,
        "recommendation_reasoning": (analysis.recommendation_reasoning or "").strip(),
        "next_step": (analysis.next_step or "").strip(),
        "scores": [
            {
                "category": s.category,
                "level": s.level.value,
                "reasoning": (s.reasoning or "").strip(),
            }
            for s in analysis.scores
        ],
    }


async def save_validation_snapshot_for_idea(
    repo: Any, idea_id: int, analysis: AnalysisResult
) -> None:
    try:
        snap = build_validation_snapshot_dict(analysis)
        await repo.save_validation_snapshot(idea_id, snap)
    except Exception:
        logger.warning("Validierungs-Snapshot konnte nicht gespeichert werden", exc_info=True)


def _format_date_short(raw: str | None) -> str:
    if not raw:
        return "—"
    s = str(raw).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s[:16]


def _trunc(text: str | None, max_len: int) -> str:
    if not text:
        return "—"
    t = " ".join(str(text).split())
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def _status_emoji_for_idea(idea: dict[str, Any], has_outcome: bool) -> str:
    if has_outcome:
        return "🏆"
    st = (idea.get("status") or "brainstorm").lower()
    return _STATUS_EMOJI_PLAIN.get(st, "❓")


def _recommendation_line_from_snapshot(snap: dict[str, Any] | None) -> str | None:
    if not snap:
        return None
    key = str(snap.get("recommendation") or "").lower().strip()
    label = _RECOMMENDATION_DE.get(key, key or "—")
    em = _RECOMMENDATION_EMOJI.get(key, "❔")
    return f"{em} Empfehlung: {label}"


def _format_snapshot_for_detail(snap: dict[str, Any] | None) -> list[str]:
    if not snap:
        return [
            "📊 Scores & Empfehlung: Keine gespeicherten Validierungsdaten "
            "(ab jetzt bei jeder Validierung gespeichert; ältere Ideen ggf. ohne Eintrag)."
        ]
    lines: list[str] = []
    rec = str(snap.get("recommendation") or "").lower().strip()
    label = _RECOMMENDATION_DE.get(rec, rec or "—")
    em = _RECOMMENDATION_EMOJI.get(rec, "")
    lines.append(f"{em} Empfehlung: {label}")
    reason = (snap.get("recommendation_reasoning") or "").strip()
    if reason:
        lines.append(f"Begründung: {reason}")
    scores = snap.get("scores")
    if isinstance(scores, list) and scores:
        lines.append("")
        lines.append("Kategorie-Scores:")
        for item in scores:
            if not isinstance(item, dict):
                continue
            cat = str(item.get("category") or "")
            cat_label = SCORING_CATEGORY_LABELS.get(cat, cat)
            lv = str(item.get("level") or "").lower()
            sem = _SCORE_LEVEL_EMOJI.get(lv, "⚪")
            rs = (str(item.get("reasoning") or "")).strip().replace("\n", " ")
            lines.append(f"{sem} {cat_label}: {rs}")
    ns = (snap.get("next_step") or "").strip()
    if ns:
        lines.append("")
        lines.append(f"Nächster Schritt: {ns}")
    return lines


def _format_idea_detail_text(
    idea: dict[str, Any],
    snap: dict[str, Any] | None,
    outcomes: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append("📌 Ideen-Details\n")
    title = idea.get("problem_statement") or idea.get("raw_idea") or "Ohne Titel"
    lines.append(f"Problem: {title}")
    lines.append(f"Status: {idea.get('status') or '—'}")
    lines.append(f"Erstellt: {_format_date_short(idea.get('created_at'))}")
    lines.append("")
    fields = [
        ("Zielgruppe", "target_audience"),
        ("Lösung", "solution"),
        ("Monetarisierung", "monetization"),
        ("Distribution", "distribution"),
        ("Unfair Advantage", "unfair_advantage"),
        ("Persona", "persona"),
        ("Aktuelle Lösung", "current_solution"),
        ("Switch-Trigger", "switch_trigger"),
    ]
    for label, key in fields:
        val = idea.get(key)
        if val:
            lines.append(f"{label}: {val}")
    lines.append("")
    lines.extend(_format_snapshot_for_detail(snap))
    lines.append("")
    if outcomes:
        lines.append("Bisherige Outcomes:")
        for o in outcomes:
            oc = o.get("outcome") or "—"
            note = (o.get("notes") or "").strip()
            when = _format_date_short(o.get("recorded_at"))
            olab = _OUTCOME_LABELS_DE.get(str(oc), str(oc))
            extra = f" — {note}" if note else ""
            lines.append(f"• {when}: {olab}{extra}")
    else:
        lines.append("Outcomes: Noch keins erfasst.")
    return "\n".join(lines)


def _outcome_keyboard(idea_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                "🚀 Gebaut & läuft", callback_data=f"outcome_{idea_id}_built"
            ),
            InlineKeyboardButton(
                "🔄 Gepivoted", callback_data=f"outcome_{idea_id}_pivoted"
            ),
        ],
        [
            InlineKeyboardButton(
                "⏸️ Pausiert", callback_data=f"outcome_{idea_id}_paused"
            ),
            InlineKeyboardButton(
                "❌ Verworfen", callback_data=f"outcome_{idea_id}_dropped"
            ),
        ],
        [
            InlineKeyboardButton(
                "💡 Noch offen", callback_data=f"outcome_{idea_id}_open"
            ),
        ],
    ]
    return InlineKeyboardMarkup(rows)


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    repo = context.bot_data.get("repository")
    if not repo:
        await update.message.reply_text("Datenbank nicht verfügbar.")
        return
    chat_id = update.effective_chat.id
    ideas = await repo.get_ideas_by_chat(chat_id)
    if not ideas:
        await update.message.reply_text(
            "Du hast noch keine Ideen in der Historie.\n\n"
            "Starte mit /idea — ich freu mich auf deinen ersten Pitch!"
        )
        return

    total = len(ideas)
    max_shown = 10
    if len(ideas) > max_shown:
        ideas = ideas[:max_shown]

    lines: list[str] = ["📋 Deine Ideen\n"]
    if total > max_shown:
        lines.append(f"(Die neuesten {max_shown} von {total} Ideen.)\n")
    keyboard_rows: list[list[InlineKeyboardButton]] = []

    for idea in ideas:
        iid = int(idea["id"])
        outcomes = await repo.get_outcomes_for_idea(iid)
        has_o = len(outcomes) > 0
        em = _status_emoji_for_idea(idea, has_o)
        title = _trunc(idea.get("problem_statement") or idea.get("raw_idea"), 56)
        when = _format_date_short(idea.get("created_at"))
        row_lines = [f"{em} #{iid} {title}", f"   📅 {when}"]
        st = (idea.get("status") or "").lower()
        if st == "validated":
            snap = await repo.get_validation_snapshot(iid)
            rec_line = _recommendation_line_from_snapshot(snap)
            if rec_line:
                row_lines.append(f"   {rec_line}")
            else:
                row_lines.append("   ✅ validiert (Scores nicht in DB)")
        lines.append("\n".join(row_lines))
        lines.append("")
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    "📊 Details", callback_data=f"history_detail_{iid}"
                ),
                InlineKeyboardButton(
                    "📝 Outcome eintragen", callback_data=f"history_outcome_{iid}"
                ),
            ]
        )

    body = "\n".join(lines).strip()
    chunks = _split_chunks(body)
    first_kb = InlineKeyboardMarkup(keyboard_rows)
    await update.message.reply_text(chunks[0], reply_markup=first_kb)
    for extra in chunks[1:]:
        await update.message.reply_text(extra)


async def cmd_learn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    repo = context.bot_data.get("repository")
    if not repo:
        await update.message.reply_text("Datenbank nicht verfügbar.")
        return
    ideas = await repo.get_ideas_by_chat(update.effective_chat.id, limit=1)
    if not ideas:
        await update.message.reply_text(
            "Noch keine Idee gespeichert.\n\n"
            "Nutze /idea für eine neue Idee — danach kannst du hier das Outcome festhalten."
        )
        return
    idea = ideas[0]
    iid = int(idea["id"])
    title = _trunc(idea.get("problem_statement") or idea.get("raw_idea"), 120)
    when = _format_date_short(idea.get("created_at"))
    outcomes = await repo.get_outcomes_for_idea(iid)
    hint = ""
    if outcomes:
        hint = (
            "\n\nHinweis: Für diese Idee gibt es schon einen Outcome-Eintrag — "
            "du kannst trotzdem einen weiteren erfassen (z. B. Update)."
        )
    text = (
        f"🎓 Lernen aus der letzten Idee\n\n"
        f"#{iid} {title}\n"
        f"📅 {when}{hint}\n\n"
        "Was ist passiert? Wähle unten eine Option oder schau unter /history nach älteren Ideen."
    )
    await update.message.reply_text(
        text,
        reply_markup=_outcome_keyboard(iid),
    )


async def handle_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = (query.data or "").strip()
    repo = context.bot_data.get("repository")
    chat = update.effective_chat
    chat_id = chat.id if chat else None

    try:
        await query.answer()
    except Exception:
        logger.debug("answer callback failed", exc_info=True)

    if not repo or chat_id is None:
        try:
            await query.edit_message_text("Bot nicht bereit. Bitte später erneut.")
        except Exception:
            pass
        return

    m = _RE_HISTORY_DETAIL.match(data)
    if m:
        idea_id = int(m.group(1))
        idea = await repo.get_idea(idea_id)
        if not idea or int(idea["telegram_chat_id"]) != chat_id:
            try:
                await query.edit_message_text("❌ Idee nicht gefunden.")
            except Exception:
                await context.bot.send_message(chat_id=chat_id, text="❌ Idee nicht gefunden.")
            return
        snap = await repo.get_validation_snapshot(idea_id)
        outcomes = await repo.get_outcomes_for_idea(idea_id)
        detail = _format_idea_detail_text(idea, snap, outcomes)
        for i, chunk in enumerate(_split_chunks(detail)):
            if i == 0:
                try:
                    await query.edit_message_text(chunk)
                except Exception:
                    await context.bot.send_message(chat_id=chat_id, text=chunk)
            else:
                await context.bot.send_message(chat_id=chat_id, text=chunk)
        return

    m = _RE_HISTORY_OUTCOME.match(data)
    if m:
        idea_id = int(m.group(1))
        idea = await repo.get_idea(idea_id)
        if not idea or int(idea["telegram_chat_id"]) != chat_id:
            try:
                await query.edit_message_text("❌ Idee nicht gefunden.")
            except Exception:
                await context.bot.send_message(chat_id=chat_id, text="❌ Idee nicht gefunden.")
            return
        outcomes = await repo.get_outcomes_for_idea(idea_id)
        extra = ""
        if outcomes:
            extra = (
                "\n\nEs gibt bereits einen Eintrag — du kannst einen weiteren Outcome hinzufügen."
            )
        text = (
            f"📝 Outcome für Idee #{idea_id}\n\n"
            "Was ist passiert? Bitte wähle eine Option:"
            f"{extra}"
        )
        try:
            await query.edit_message_text(text, reply_markup=_outcome_keyboard(idea_id))
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=_outcome_keyboard(idea_id),
            )
        return

    m = _RE_OUTCOME_PICK.match(data)
    if m:
        idea_id = int(m.group(1))
        status = m.group(2)
        idea = await repo.get_idea(idea_id)
        if not idea or int(idea["telegram_chat_id"]) != chat_id:
            try:
                await query.edit_message_text("❌ Idee nicht gefunden.")
            except Exception:
                await context.bot.send_message(chat_id=chat_id, text="❌ Idee nicht gefunden.")
            return
        label_de = _OUTCOME_LABELS_DE.get(status, status)
        context.user_data["outcome_notes_pending"] = {
            "idea_id": idea_id,
            "outcome": status,
            "label_de": label_de,
        }
        prompt = (
            f"Du hast „{label_de}“ gewählt (Idee #{idea_id}).\n\n"
            "Optional: Schick mir eine kurze Notiz (was ist passiert, Zahlen, Learnings). "
            "Zum Überspringen: /skip_outcome"
        )
        try:
            await query.edit_message_text(prompt)
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text=prompt)
        return

    logger.warning("Unbekannte History-Callback-Daten: %s", data)


async def _finalize_outcome_with_notes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    notes: str | None,
) -> None:
    """Speichert Outcome aus outcome_notes_pending und bestätigt."""
    if not update.message:
        return
    pending = context.user_data.pop("outcome_notes_pending", None)
    if not isinstance(pending, dict):
        await update.message.reply_text("Kein ausstehendes Outcome mehr.")
        return
    repo = context.bot_data.get("repository")
    if not repo:
        await update.message.reply_text("Datenbank nicht verfügbar.")
        return
    idea_id = int(pending["idea_id"])
    outcome_key = str(pending["outcome"])
    label_de = str(pending.get("label_de") or outcome_key)
    chat_id = update.effective_chat.id

    idea = await repo.get_idea(idea_id)
    if not idea or int(idea["telegram_chat_id"]) != chat_id:
        await update.message.reply_text("Idee nicht gefunden oder keine Berechtigung.")
        return

    try:
        oid = await repo.save_outcome(idea_id, outcome_key, notes)
    except Exception:
        logger.exception("save_outcome failed idea_id=%s", idea_id)
        await update.message.reply_text(
            "Speichern ist fehlgeschlagen. Bitte versuche es später erneut."
        )
        return

    note_line = f"\n📝 Notiz: {notes}" if notes else "\n📝 Keine Notiz."
    await update.message.reply_text(
        f"✅ Outcome gespeichert (ID {oid}).\n\n"
        f"Idee #{idea_id}: {label_de}{note_line}\n\n"
        "Danke — so wird deine Historie immer wertvoller. /history"
    )


async def handle_outcome_notes_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nach Outcome-Wahl: Freitext-Notiz speichern."""
    if not update.message:
        return
    if not context.user_data.get("outcome_notes_pending"):
        return
    raw = (update.message.text or "").strip()
    notes: str | None = raw if raw else None
    await _finalize_outcome_with_notes(update, context, notes)


async def cmd_skip_outcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Optionaler Befehl: Notiz überspringen und Outcome trotzdem speichern."""
    if not update.message:
        return
    if not context.user_data.get("outcome_notes_pending"):
        await update.message.reply_text(
            "Kein ausstehendes Outcome — nutze zuerst die Buttons unter /learn oder /history."
        )
        return
    await _finalize_outcome_with_notes(update, context, None)


def get_history_handlers(
    wrap: Callable[[HandlerCallback], HandlerCallback],
) -> list[CallbackQueryHandler]:
    """CallbackQueryHandler für ``history_*`` und ``outcome_*`` (inkl. Outcome-Auswahl)."""
    return [
        CallbackQueryHandler(
            wrap(handle_history_callback),
            pattern=r"^(history_|outcome_)",
        ),
    ]
