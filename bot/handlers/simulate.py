"""
/simulate — Persona-Simulation nach erfolgreicher Validierung (KI, kein Ersatz fuer echte Forschung).
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from modules.simulate import SimulationModule, SimulationResult
from shared.constants import TELEGRAM_MAX_MESSAGE_LENGTH
from shared.types import ConversationContext, IdeaSummary, ResearchBundle

logger = logging.getLogger(__name__)

CB_SIM_RETRY = "sim_retry"
CB_SIM_SUMMARY = "sim_summary"

MISSING_PREREQ_TEXT = (
    "Hier fehlen Daten. Bitte zuerst /idea nutzen und danach /validate ausfuehren, "
    "damit Recherche und Analyse vorliegen — danach kannst du /simulate starten."
)

DISCLAIMER_BEFORE_RUN = (
    "⚠️ HINWEIS: Die folgende Persona-Simulation ist KI-generiert.\n"
    "Sie dient als Denkanstoß und ersetzt KEINE echte Marktforschung.\n"
    "Starte Simulation..."
)


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


def _get_conv(context: ContextTypes.DEFAULT_TYPE) -> Optional[ConversationContext]:
    raw = context.user_data.get("conv_context")
    return raw if isinstance(raw, ConversationContext) else None


def _simulation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Neue Personas", callback_data=CB_SIM_RETRY),
                InlineKeyboardButton("📋 Zusammenfassung", callback_data=CB_SIM_SUMMARY),
            ]
        ]
    )


def _would_pay_token(line: str) -> str:
    s = (line or "").strip().lower()
    if s.startswith("yes"):
        return "yes"
    if s.startswith("maybe"):
        return "maybe"
    if s.startswith("no"):
        return "no"
    return "maybe"


async def _reply_chunked(
    message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    chunks = _split_text_chunks(text)
    for i, chunk in enumerate(chunks):
        kw: dict = {}
        if i == len(chunks) - 1 and reply_markup is not None:
            kw["reply_markup"] = reply_markup
        await message.reply_text(chunk, **kw)


async def _show_simulation_summary(
    update: Update, context: ContextTypes.DEFAULT_TYPE, result: SimulationResult
) -> None:
    reactions = result.reactions or []
    if not reactions:
        msg = update.effective_message
        if msg:
            await msg.reply_text("Keine Reaktionsdaten vorhanden.")
        return

    levels = [float(r.excitement_level) for r in reactions]
    avg_excitement = sum(levels) / len(levels) if levels else 0.0

    tokens = [_would_pay_token(r.would_pay) for r in reactions]
    payers = sum(1 for t in tokens if t in ("yes", "maybe"))

    concerns = [c.strip() for r in reactions for c in [r.biggest_concern] if c and c.strip()]
    most_common: str | None = None
    if concerns:
        cnt = Counter(concerns)
        most_common = cnt.most_common(1)[0][0]

    lines = [
        "📋 Aggregat (simuliert)",
        "",
        f"Durchschnittliches Excitement (1-5): {avg_excitement:.2f}",
        f"Wuerden zahlen (ja oder vielleicht): {payers} von {len(reactions)}",
    ]
    if most_common:
        lines.append(f"Haeufigstes Bedenken: {most_common}")
    else:
        lines.append("Haeufigstes Bedenken: —")

    base_text = "\n".join(lines)

    llm = context.bot_data.get("llm_client")
    extra = ""
    if llm is not None:
        try:
            sys_p = (
                "Du bist Analyst. Fasse in maximal 3 kurzen Saetzen auf Deutsch zusammen, "
                "was die simulierten Personas gemeinsam signalisieren. "
                "Nutze nur die gegebenen Kennzahlen und Stichworte — keine neuen Fakten erfinden."
            )
            user_p = (
                f"{base_text}\n\n"
                f"Personas: {', '.join(result.personas)}\n"
                f"Zahlungs-Tokens: {', '.join(tokens)}"
            )
            extra = (await llm.complete(sys_p, user_p, task="summarize", max_tokens=400)).strip()
        except Exception:
            logger.warning("LLM-Zusammenfassung fuer Simulation fehlgeschlagen", exc_info=True)

    out = base_text
    if extra:
        out = f"{base_text}\n\n💬 Einordnung:\n{extra}"

    msg = update.effective_message
    if msg:
        await msg.reply_text(out)


async def _execute_simulation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    show_disclaimer: bool,
) -> None:
    conv_ctx = _get_conv(context)
    chat = update.effective_chat
    msg = update.effective_message
    if not chat or not msg:
        return

    if (
        not conv_ctx
        or conv_ctx.current_idea_id is None
        or not isinstance(conv_ctx.idea_summary, IdeaSummary)
    ):
        await msg.reply_text(MISSING_PREREQ_TEXT)
        return

    if not isinstance(conv_ctx.research_bundle, ResearchBundle):
        await msg.reply_text(MISSING_PREREQ_TEXT)
        return

    sim_mod = context.bot_data.get("simulation_module")
    if not isinstance(sim_mod, SimulationModule):
        await msg.reply_text("Simulations-Modul ist nicht geladen. Bitte den Bot neu starten.")
        logger.error("simulation_module fehlt in bot_data")
        return

    chat_id = chat.id

    async def send_progress(text: str) -> None:
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"⏳ {text}")
        except Exception:
            logger.debug("Fortschritts-Nachricht konnte nicht gesendet werden", exc_info=True)

    if show_disclaimer:
        await msg.reply_text(DISCLAIMER_BEFORE_RUN)

    try:
        result = await sim_mod.run(
            idea_id=conv_ctx.current_idea_id,
            summary=conv_ctx.idea_summary,
            research=conv_ctx.research_bundle,
            progress=send_progress,
        )
    except Exception:
        logger.exception("Simulation fehlgeschlagen")
        await msg.reply_text(
            "Die Simulation ist fehlgeschlagen. Bitte spaeter erneut versuchen oder /validate wiederholen."
        )
        return

    context.user_data["last_simulation_result"] = result
    formatted = sim_mod.format_telegram_output(result)
    await _reply_chunked(msg, formatted, reply_markup=_simulation_keyboard())


async def cmd_simulate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await _execute_simulation(update, context, show_disclaimer=True)


async def handle_simulate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer()
    except Exception:
        logger.debug("callback_query.answer fehlgeschlagen", exc_info=True)

    data = (q.data or "").strip()
    msg = q.message
    if not msg:
        return

    if data == CB_SIM_RETRY:
        await _execute_simulation(update, context, show_disclaimer=False)
        return

    if data == CB_SIM_SUMMARY:
        raw = context.user_data.get("last_simulation_result")
        if not isinstance(raw, SimulationResult):
            await msg.reply_text(
                "Keine gespeicherte Simulation. Fuehre zuerst /simulate aus."
            )
            return
        await _show_simulation_summary(update, context, raw)
        return

    await msg.reply_text("Unbekannte Aktion.")


def get_simulate_handlers(wrap) -> list:
    return [CallbackQueryHandler(wrap(handle_simulate_callback), pattern=r"^sim_")]
