"""
Telegram handlers for Idea Roast bot.
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from shared.types import BrainstormState, BrainstormAnswers, ConversationContext

logger = logging.getLogger(__name__)

IDEA_FLOW = 0


def _get_conversation_context(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> ConversationContext:
    """Get or create a ConversationContext in user_data."""
    if "conv_context" not in context.user_data:
        context.user_data["conv_context"] = ConversationContext(telegram_chat_id=chat_id)
    return context.user_data["conv_context"]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "Willkommen bei Idea Roast! 🔥\n\n"
        "Ich bin dein kritischer Co-Founder. Ich helfe dir, Geschaeftsideen "
        "zu schaerfen und knallhart zu validieren.\n\n"
        "Befehle:\n"
        "/idea — Neue Idee brainstormen\n"
        "/validate — Aktuelle Idee validieren\n"
        "/history — Alle bisherigen Ideen\n"
        "/profile — Dein Gruender-Profil\n"
        "/help — Alle Befehle\n\n"
        "Starte mit /idea oder schick mir einfach deine Idee als Text oder Sprachnachricht."
    )


async def cmd_validate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Validierung kommt in Meilenstein 2. Starte erstmal mit /idea.")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        repo = context.bot_data.get("repository")
        if not repo:
            await update.message.reply_text("Datenbank nicht verfuegbar.")
            return
        ideas = await repo.get_ideas_by_chat(update.effective_chat.id)
        if not ideas:
            await update.message.reply_text("Noch keine Ideen gespeichert. Starte mit /idea!")
            return
        lines = ["📋 Deine bisherigen Ideen:\n"]
        for i, idea in enumerate(ideas, 1):
            status_icon = {"brainstorm": "🧠", "validated": "✅", "archived": "📦"}.get(idea["status"], "❓")
            name = idea.get("problem_statement") or idea.get("raw_idea") or "Ohne Titel"
            lines.append(f"{i}. {status_icon} {name[:60]}")
        await update.message.reply_text("\n".join(lines))


async def cmd_learn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Outcome-Tracking kommt in Meilenstein 4.")


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Profil-Management kommt in Meilenstein 4.")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Einstellungen kommen in einem spaeteren Update.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "📖 Idea Roast — Befehle\n\n"
            "/idea — Neue Idee brainstormen\n"
            "/validate — Aktuelle Idee validieren lassen\n"
            "/history — Alle bisherigen Ideen und Scores\n"
            "/learn — Outcome einer umgesetzten Idee eintragen\n"
            "/profile — Dein Gruender-Profil anzeigen/bearbeiten\n"
            "/settings — Research-Quellen konfigurieren\n"
            "/cancel — Aktuellen Vorgang abbrechen\n\n"
            "Du kannst auch jederzeit Sprachnachrichten schicken."
        )


async def idea_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END

    chat_id = update.effective_chat.id
    conv_ctx = ConversationContext(
        telegram_chat_id=chat_id,
        brainstorm_state=BrainstormState.AWAITING_IDEA,
        brainstorm_answers=BrainstormAnswers(),
    )
    context.user_data["conv_context"] = conv_ctx

    await update.message.reply_text(
        "Hey! Was hast du im Kopf? "
        "Beschreib mir die Idee in 2-3 Saetzen — muss nicht perfekt sein.\n\n"
        "💡 Du kannst auch eine Sprachnachricht schicken.\n"
        "/cancel zum Abbrechen."
    )
    return IDEA_FLOW


async def idea_conversation_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return IDEA_FLOW

    text = update.message.text.strip()
    if not text:
        return IDEA_FLOW

    return await _process_brainstorm_input(update, context, text)


async def _process_brainstorm_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> int:
    """Core brainstorm logic - works for both text and voice input."""
    conv_ctx: Optional[ConversationContext] = context.user_data.get("conv_context")
    if not conv_ctx:
        await update.message.reply_text("Bitte starte zuerst mit /idea.")
        return ConversationHandler.END

    brainstorm = context.bot_data.get("brainstorm_module")
    repo = context.bot_data.get("repository")

    if not brainstorm:
        await update.message.reply_text("Bot nicht vollstaendig initialisiert. Bitte spaeter erneut versuchen.")
        return ConversationHandler.END

    bot_response, new_state = await brainstorm.process_message(conv_ctx, text)
    conv_ctx.brainstorm_state = new_state

    if new_state == BrainstormState.SUMMARIZING:
        await update.message.reply_text(bot_response)
        summary = await brainstorm.generate_summary(conv_ctx)
        conv_ctx.idea_summary = summary

        summary_text = (
            "📋 IDEEN-ZUSAMMENFASSUNG\n\n"
            f"Problem: {summary.problem_statement}\n"
            f"Zielgruppe: {summary.target_audience}\n"
            f"Loesung: {summary.solution}\n"
            f"Monetarisierung: {summary.monetization}\n"
            f"Distribution: {summary.distribution_channel}\n"
            f"Dein Vorteil: {summary.unfair_advantage}\n\n"
            "Stimmt das so? Soll ich die Idee validieren? (Ja/Nein)"
        )
        await update.message.reply_text(summary_text)
        conv_ctx.brainstorm_state = BrainstormState.AWAITING_CONFIRMATION
        return IDEA_FLOW

    if new_state == BrainstormState.AWAITING_CONFIRMATION:
        return IDEA_FLOW

    if conv_ctx.brainstorm_state == BrainstormState.AWAITING_CONFIRMATION:
        lower = text.lower().strip()
        if lower in ("ja", "yes", "jo", "klar", "los", "validieren", "ja!"):
            if repo and conv_ctx.idea_summary:
                idea_id = await repo.create_idea(
                    conv_ctx.telegram_chat_id,
                    conv_ctx.brainstorm_answers.raw_idea,
                )
                s = conv_ctx.idea_summary
                await repo.update_idea(
                    idea_id,
                    persona=conv_ctx.brainstorm_answers.persona,
                    current_solution=conv_ctx.brainstorm_answers.current_solution,
                    switch_trigger=conv_ctx.brainstorm_answers.switch_trigger,
                    monetization=conv_ctx.brainstorm_answers.monetization,
                    distribution=conv_ctx.brainstorm_answers.distribution,
                    problem_statement=s.problem_statement,
                    target_audience=s.target_audience,
                    solution=s.solution,
                    unfair_advantage=s.unfair_advantage,
                    status="brainstorm",
                )
                conv_ctx.current_idea_id = idea_id
            await update.message.reply_text(
                "Idee gespeichert! ✅\n"
                "Validierung mit echten Daten kommt in Meilenstein 2.\n"
                "Nutze /idea fuer eine weitere Idee."
            )
            conv_ctx.brainstorm_state = BrainstormState.DONE
            return ConversationHandler.END
        elif lower in ("nein", "no", "ne", "nee", "nicht"):
            await update.message.reply_text(
                "Okay, was soll ich aendern? Beschreib kurz was anders ist, "
                "dann passe ich die Zusammenfassung an."
            )
            return IDEA_FLOW
        else:
            await update.message.reply_text("Kurz Ja oder Nein — stimmt die Zusammenfassung?")
            return IDEA_FLOW

    await update.message.reply_text(bot_response)
    return IDEA_FLOW


async def idea_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Brainstorm abgebrochen. Starte jederzeit neu mit /idea.")
    context.user_data.pop("conv_context", None)
    return ConversationHandler.END


async def handle_text_content(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Process text outside of a conversation flow."""
    if update.message:
        await update.message.reply_text(
            "Schick mir /idea um eine neue Idee zu brainstormen, "
            "oder /help fuer alle Befehle."
        )


async def dispatch_transcribed_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Route voice transcription based on conversation state."""
    conv_ctx: Optional[ConversationContext] = context.user_data.get("conv_context")
    if conv_ctx and conv_ctx.brainstorm_state not in (BrainstormState.DONE, BrainstormState.AWAITING_IDEA):
        await _process_brainstorm_input(update, context, text)
    else:
        await handle_text_content(update, context, text)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    raw = update.message.text or update.message.caption or ""
    if not raw.strip():
        return
    await dispatch_transcribed_text(update, context, raw.strip())


from .voice import handle_voice  # noqa: E402, F401
