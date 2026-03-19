"""Telegram bot entry point (polling)."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot import handlers
from bot.config import Settings, load_settings
from bot.handlers.deep_dive import handle_deep_dive_callback
from bot.handlers.history import cmd_skip_outcome, get_history_handlers
from bot.handlers.profile import get_profile_handlers
from bot.handlers.simulate import cmd_simulate, get_simulate_handlers
from bot.handlers.stats import cmd_stats
from shared.logging_config import setup_logging

logger = logging.getLogger(__name__)

HandlerCallback = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]


async def ensure_allowed(update: Update, settings: Settings) -> bool:
    """If ALLOWED_USER_IDS is set, only those users may use the bot."""
    allowed = settings.allowed_user_ids
    if not allowed:
        return True
    user = update.effective_user
    uid = user.id if user else None
    if uid is not None and uid in allowed:
        return True
    if update.message:
        await update.message.reply_text(
            "Access denied. Your user ID is not in ALLOWED_USER_IDS."
        )
    return False


def with_access_control(
    settings: Settings,
    *,
    conversation: bool = False,
) -> Callable[[HandlerCallback], HandlerCallback]:
    """
    Wrap a handler: deny when ALLOWED_USER_IDS is non-empty and user not listed.
    For ConversationHandler callbacks, return ConversationHandler.END when denied.
    """

    def decorator(handler: HandlerCallback) -> HandlerCallback:
        async def wrapped(
            update: Update, context: ContextTypes.DEFAULT_TYPE
        ) -> Any:
            if not await ensure_allowed(update, settings):
                return ConversationHandler.END if conversation else None
            return await handler(update, context)

        return wrapped

    return decorator


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(
        "Exception while handling an update",
        exc_info=context.error,
        extra={"update": update},
    )


def _build_application(settings: Settings) -> Application:
    ac = with_access_control(settings, conversation=False)
    acc = with_access_control(settings, conversation=True)

    idea_conversation = ConversationHandler(
        entry_points=[CommandHandler("idea", acc(handlers.idea_entry))],
        states={
            handlers.IDEA_FLOW: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    acc(handlers.idea_conversation_text),
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", acc(handlers.idea_cancel))],
        name="idea_flow",
        persistent=False,
    )

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )

    # 1) Commands (ConversationHandler covers /idea; other commands here)
    application.add_handler(idea_conversation)
    application.add_handler(CommandHandler("start", ac(handlers.cmd_start)))
    application.add_handler(CommandHandler("validate", ac(handlers.cmd_validate)))
    application.add_handler(CommandHandler("simulate", ac(cmd_simulate)))
    application.add_handler(CommandHandler("history", ac(handlers.cmd_history)))
    application.add_handler(CommandHandler("learn", ac(handlers.cmd_learn)))
    application.add_handler(CommandHandler("skip_outcome", ac(cmd_skip_outcome)))
    application.add_handler(CommandHandler("profile", ac(handlers.cmd_profile)))
    application.add_handler(CommandHandler("settings", ac(handlers.cmd_settings)))
    application.add_handler(CommandHandler("stats", ac(cmd_stats)))
    application.add_handler(CommandHandler("help", ac(handlers.cmd_help)))

    application.add_handler(
        CallbackQueryHandler(ac(handle_deep_dive_callback), pattern=r"^deep_")
    )

    for h in get_profile_handlers(ac):
        application.add_handler(h)

    for h in get_history_handlers(ac):
        application.add_handler(h)

    for h in get_simulate_handlers(ac):
        application.add_handler(h)

    # 2) Voice
    application.add_handler(MessageHandler(filters.VOICE, ac(handlers.handle_voice)))

    # 3) Free text (not commands)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, ac(handlers.handle_text))
    )

    application.add_error_handler(error_handler)
    return application


async def post_init(application: Application) -> None:
    """Initialize DB, repository, tool clients, and modules after bot startup."""
    settings: Settings = application.bot_data["settings"]

    from db.models import init_db
    from db.repository import Repository
    from llm.client import LLMClient
    from modules.analysis import AnalysisModule
    from modules.brainstorm import BrainstormModule
    from modules.profile import ProfileModule
    from modules.report import ReportModule
    from modules.research import ResearchModule
    from modules.simulate import SimulationModule
    from tools.github_search import GitHubSearchClient
    from tools.hackernews import HackerNewsClient
    from tools.producthunt import ProductHuntClient
    from tools.reddit import RedditClient
    from tools.searxng import SearXNGClient
    from tools.trend_radar import TrendRadar

    await init_db(settings.database_path)
    repo = Repository(settings.database_path)
    await repo.connect()

    llm_client = LLMClient(
        anthropic_api_key=settings.anthropic_api_key,
        openai_api_key=settings.openai_api_key,
    )
    brainstorm = BrainstormModule(llm_client=llm_client)

    searxng = SearXNGClient(base_url=settings.searxng_base_url)
    reddit = RedditClient(
        client_id=settings.reddit_client_id or "",
        client_secret=settings.reddit_client_secret or "",
        user_agent=settings.reddit_user_agent or "IdeaRoast/1.0",
    )
    hackernews = HackerNewsClient()
    github = GitHubSearchClient(token=settings.github_token)
    producthunt = ProductHuntClient(searxng_base_url=settings.searxng_base_url)
    trend_radar = TrendRadar(llm_client=llm_client)

    research = ResearchModule(
        searxng=searxng,
        reddit=reddit,
        hackernews=hackernews,
        github=github,
        producthunt=producthunt,
        trend_radar=trend_radar,
        llm=llm_client,
        repo=repo,
    )

    analysis = AnalysisModule(llm=llm_client)
    report = ReportModule(llm=llm_client)
    profile = ProfileModule(llm=llm_client, repo=repo)
    simulation = SimulationModule(llm_client=llm_client)

    application.bot_data["repository"] = repo
    application.bot_data["llm_client"] = llm_client
    application.bot_data["brainstorm_module"] = brainstorm
    application.bot_data["research_module"] = research
    application.bot_data["analysis_module"] = analysis
    application.bot_data["report_module"] = report
    application.bot_data["profile_module"] = profile
    application.bot_data["simulation_module"] = simulation
    application.bot_data["_tool_clients"] = [
        searxng, reddit, hackernews, github, producthunt, trend_radar,
    ]
    logger.info("DB initialized, all modules + tool clients loaded")


async def post_shutdown(application: Application) -> None:
    for client in application.bot_data.get("_tool_clients", []):
        try:
            await client.close()
        except Exception:
            logger.debug("Error closing tool client", exc_info=True)
    repo = application.bot_data.get("repository")
    if repo:
        await repo.close()
    logger.info("Repository + tool clients closed")


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    application = _build_application(settings)
    application.bot_data["settings"] = settings
    application.post_init = post_init
    application.post_shutdown = post_shutdown
    logger.info("Starting bot (polling)")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
