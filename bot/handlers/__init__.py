"""
Telegram handlers for Idea Roast bot.
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from shared.types import BrainstormState, BrainstormAnswers, ConversationContext

from .history import cmd_history, cmd_learn, save_validation_snapshot_for_idea

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
        "Ich bin dein kritischer Co-Founder — ich helfe dir, "
        "Geschaeftsideen zu schaerfen und knallhart zu validieren.\n\n"
        "/idea — Beschreib deine Idee, ich stelle gezielte Fragen\n"
        "/validate — Validierung mit echten Daten aus 8+ Quellen\n"
        "/simulate — Simulierte Personas reagieren auf deine Idee\n"
        "/history — Alle bisherigen Ideen mit Bewertungen\n"
        "/profile — Dein Gruender-Profil (lernt mit der Zeit)\n"
        "/stats — Bot-Status und Nutzungsstatistiken\n"
        "/help — Alle Befehle im Detail\n\n"
        "Leg los mit /idea oder schick mir direkt deine Idee."
    )


async def cmd_validate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    conv_ctx: Optional[ConversationContext] = context.user_data.get("conv_context")
    if not conv_ctx or not conv_ctx.current_idea_id or not conv_ctx.idea_summary:
        await update.message.reply_text(
            "Keine Idee zum Validieren vorhanden. Starte mit /idea."
        )
        return

    research_mod = context.bot_data.get("research_module")
    analysis_mod = context.bot_data.get("analysis_module")
    report_mod = context.bot_data.get("report_module")
    repo = context.bot_data.get("repository")

    if not research_mod or not analysis_mod or not report_mod:
        await update.message.reply_text("Module nicht vollstaendig geladen.")
        return

    chat_id = update.effective_chat.id
    idea_id = conv_ctx.current_idea_id
    summary = conv_ctx.idea_summary

    async def send_progress(msg: str) -> None:
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"⏳ {msg}")
        except Exception:
            pass

    await update.message.reply_text(
        "🔍 Starte Validierung — das kann 1-2 Minuten dauern.\n"
        "Ich halte dich auf dem Laufenden."
    )

    # Phase 1: Research
    bundle = await research_mod.run(
        idea_id=idea_id,
        summary=summary,
        progress=send_progress,
    )
    conv_ctx.research_bundle = bundle

    # Phase 2: Analysis (Scoring + Devils Advocate + Out-of-Box)
    analysis = await analysis_mod.run(
        idea_id=idea_id,
        summary=summary,
        research=bundle,
        progress=send_progress,
    )
    conv_ctx.analysis_result = analysis

    # Phase 3: Report
    await send_progress("Report wird erstellt...")

    from bot.handlers.deep_dive import build_report_keyboard

    report_obj = await report_mod.create_full_report(
        idea_id=idea_id,
        summary=summary,
        research=bundle,
        analysis=analysis,
    )
    context.user_data["last_export_path"] = report_obj.export_file_path
    context.user_data["last_validation_report"] = report_obj

    telegram_text = await report_mod.generate_telegram_report(
        summary=summary,
        research=bundle,
        analysis=analysis,
    )
    await update.message.reply_text(telegram_text, reply_markup=build_report_keyboard())

    if bundle.trend_radar.chart_image_path:
        try:
            with open(bundle.trend_radar.chart_image_path, "rb") as f:
                await context.bot.send_photo(chat_id=chat_id, photo=f)
        except Exception:
            logger.warning("Could not send trend chart", exc_info=True)

    # Update idea status in DB
    if repo:
        try:
            await repo.update_idea(idea_id, status="validated")
        except Exception:
            logger.warning("Could not update idea status", exc_info=True)
        await save_validation_snapshot_for_idea(repo, idea_id, analysis)

    profile_mod = context.bot_data.get("profile_module")
    if profile_mod and conv_ctx.idea_summary:
        try:
            uid = update.effective_user.id if update.effective_user else chat_id
            await profile_mod.update_from_conversation(
                uid, conv_ctx.idea_summary, conv_ctx.analysis_result
            )
        except Exception:
            logger.warning("Profile update after validation failed", exc_info=True)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Einstellungen kommen in einem spaeteren Update.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "📖 Idea Roast — Befehle\n\n"
            "/idea — Neue Idee brainstormen\n"
            "/validate — Aktuelle Idee validieren lassen\n"
            "/simulate — Persona-Simulation (nach /validate)\n"
            "/history — Alle bisherigen Ideen und Scores\n"
            "/learn — Outcome einer umgesetzten Idee eintragen\n"
            "/skip_outcome — Notiz beim Outcome ueberspringen\n"
            "/profile — Dein Gruender-Profil anzeigen/bearbeiten\n"
            "/settings — Research-Quellen konfigurieren\n"
            "/stats — Bot-Metriken und Systemstatus\n"
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

    if conv_ctx.brainstorm_state == BrainstormState.AWAITING_CONFIRMATION:
        lower = text.lower().strip()
        if lower in ("ja", "yes", "jo", "klar", "los", "validieren", "ja!", "jap", "yep", "sure", "ok", "okay"):
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
                "Idee gespeichert! ✅\n\n"
                "Nutze /validate um die Idee mit echten Daten zu validieren,\n"
                "oder /idea fuer eine weitere Idee."
            )
            conv_ctx.brainstorm_state = BrainstormState.DONE
            return ConversationHandler.END
        elif lower in ("nein", "no", "ne", "nee", "nicht", "nope"):
            conv_ctx.brainstorm_state = BrainstormState.IN_PROGRESS
            await update.message.reply_text(
                "Okay, was soll ich aendern? Beschreib kurz was anders ist, "
                "dann passe ich die Zusammenfassung an."
            )
            return IDEA_FLOW
        else:
            await update.message.reply_text("Kurz Ja oder Nein — stimmt die Zusammenfassung?")
            return IDEA_FLOW

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
    if context.user_data.get("outcome_notes_pending"):
        from .history import handle_outcome_notes_message

        await handle_outcome_notes_message(update, context)
        return
    if context.user_data.get("awaiting_deep_dive_question"):
        from .deep_dive import handle_deep_dive_text

        await handle_deep_dive_text(update, context)
        return
    if context.user_data.get("awaiting_profile_text"):
        from .profile import handle_profile_text

        await handle_profile_text(update, context)
        return
    await dispatch_transcribed_text(update, context, raw.strip())


from .profile import cmd_profile  # noqa: E402, F401
from .voice import handle_voice  # noqa: E402, F401
