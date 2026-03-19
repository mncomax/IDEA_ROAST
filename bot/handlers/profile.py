"""Telegram handlers for /profile and founder-profile learning."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from modules.profile import ProfileModule, idea_summary_from_idea_row

logger = logging.getLogger(__name__)

HandlerCallback = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]

PROFILE_EDIT_CB = "profile_edit"
PROFILE_LEARN_CB = "profile_learn"


def _profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✏️ Profil bearbeiten", callback_data=PROFILE_EDIT_CB
                )
            ],
            [
                InlineKeyboardButton(
                    "🔄 Aus Ideen lernen", callback_data=PROFILE_LEARN_CB
                )
            ],
        ]
    )


def _get_profile_module(context: ContextTypes.DEFAULT_TYPE) -> ProfileModule | None:
    mod = context.bot_data.get("profile_module")
    return mod if isinstance(mod, ProfileModule) else None


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    mod = _get_profile_module(context)
    if not mod:
        await update.message.reply_text("Profil-Modul nicht geladen.")
        return

    uid = update.effective_user.id if update.effective_user else None
    if uid is None:
        await update.message.reply_text("Kein Benutzer-Kontext.")
        return

    try:
        name = (update.effective_user.first_name or "") if update.effective_user else ""
        profile = await mod.get_or_create_profile(uid, name=name)
        text = await mod.format_profile_text(profile)
        await update.message.reply_text(text, reply_markup=_profile_keyboard())
    except Exception:
        logger.exception("cmd_profile failed")
        await update.message.reply_text(
            "Das Profil konnte gerade nicht geladen werden. Bitte später erneut versuchen."
        )


async def handle_profile_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query:
        return

    try:
        await query.answer()
    except Exception:
        logger.debug("callback answer failed", exc_info=True)

    data = query.data or ""
    mod = _get_profile_module(context)
    repo = context.bot_data.get("repository")
    chat = update.effective_chat
    user = update.effective_user

    if not mod or not repo or not chat or not user:
        if query.message:
            try:
                await query.message.reply_text("Bot nicht vollständig initialisiert.")
            except Exception:
                logger.debug("reply after callback failed", exc_info=True)
        return

    uid = user.id

    if data == PROFILE_EDIT_CB:
        context.user_data["awaiting_profile_text"] = True
        text = (
            "✏️ Profil bearbeiten\n\n"
            "Schreib mir in einer Nachricht, wer du bist: Skills, Branche, "
            "Technologien, wie viel Zeit du hast, wie risikofreudig du bist — "
            "freier Text.\n\n"
            "Beispiel: „Ich bin Fullstack-Entwickler, 5 Jahre E-Commerce, "
            "Stack React und Python, ca. 10h pro Woche.“"
        )
        if query.message:
            try:
                await query.message.reply_text(text)
            except Exception:
                logger.exception("profile_edit reply failed")
        return

    if data == PROFILE_LEARN_CB:
        try:
            ideas = await repo.get_ideas_by_chat(chat.id, limit=100)
        except Exception:
            logger.exception("get_ideas_by_chat for profile_learn")
            if query.message:
                await query.message.reply_text(
                    "Ideen konnten nicht geladen werden. Bitte später erneut versuchen."
                )
            return

        validated = [i for i in ideas if i.get("status") == "validated"]
        validated.reverse()

        if not validated:
            if query.message:
                await query.message.reply_text(
                    "Es gibt noch keine validierten Ideen. "
                    "Nutze /idea und /validate — danach kann ich aus den Ideen lernen."
                )
            return

        if query.message:
            await query.message.reply_text(
                f"🔄 Ich lerne aus {len(validated)} validierter Idee(n) — einen Moment…"
            )

        last_profile = await mod.get_or_create_profile(
            uid, name=(user.first_name or "")
        )
        errors = 0
        for idea_row in validated:
            summary = idea_summary_from_idea_row(idea_row)
            try:
                last_profile = await mod.update_from_conversation(
                    uid, summary, analysis=None
                )
            except Exception:
                errors += 1
                logger.warning(
                    "profile_learn failed for idea_id=%s", idea_row.get("id"), exc_info=True
                )

        try:
            body = await mod.format_profile_text(last_profile)
        except Exception:
            logger.exception("format_profile_text after learn")
            if query.message:
                await query.message.reply_text(
                    "Profil wurde aktualisiert, Anzeige ist aber fehlgeschlagen."
                )
            return

        suffix = f"\n\n⚠️ Bei {errors} Idee(n) ist die Extraktion ausgefallen." if errors else ""
        if query.message:
            await query.message.reply_text(
                "✅ Profil aus deinen Ideen angereichert." + suffix + "\n\n" + body
            )
        return

    logger.warning("Unknown profile callback: %s", data)


async def handle_profile_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    mod = _get_profile_module(context)
    uid = update.effective_user.id if update.effective_user else None
    if not mod or uid is None:
        context.user_data.pop("awaiting_profile_text", None)
        await update.message.reply_text("Profil-Modul nicht verfügbar.")
        return

    context.user_data.pop("awaiting_profile_text", None)

    try:
        confirm, profile = await mod.interactive_profile_update(
            uid, update.message.text
        )
        body = await mod.format_profile_text(profile)
        await update.message.reply_text(f"{confirm}\n\n{body}")
    except Exception:
        logger.exception("handle_profile_text failed")
        await update.message.reply_text(
            "Beim Verarbeiten deiner Nachricht ist ein Fehler aufgetreten."
        )


def get_profile_handlers(
    wrap: Callable[[HandlerCallback], HandlerCallback],
) -> list[CallbackQueryHandler[Any]]:
    """
    CallbackQueryHandler für ``profile_*`` (z. B. profile_edit, profile_learn).

    ``wrap`` ist z. B. ``with_access_control(settings)(...)`` wie bei anderen Handlern.
    """
    return [
        CallbackQueryHandler(wrap(handle_profile_callback), pattern=r"^profile_"),
    ]
