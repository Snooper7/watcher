import logging

from telegram import BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from bot.config import Settings
from bot.database.db import init_db
from bot.logger import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging()
    logger.info("[main] Starting Whatcher bot")

    settings = Settings()
    logger.debug("[main] Settings loaded: LOG_LEVEL=%s", settings.LOG_LEVEL)

    init_db(settings.DATABASE_URL)
    logger.info("[main] Database initialized")

    app = Application.builder().token(settings.BOT_TOKEN).build()
    logger.info("[main] Telegram Application created. Bot token: ***%s", settings.BOT_TOKEN[-4:])

    _register_handlers(app, settings)

    async def _post_init(application: Application) -> None:
        await application.bot.set_my_commands([
            BotCommand("start", "Начать работу с ботом"),
            BotCommand("help", "Список команд"),
            BotCommand("check", "Найти самый дешёвый товар по бренду и фильтрам"),
            BotCommand("favorites", "Ваши сохранённые запросы"),
            BotCommand("cancel", "Отменить текущий поиск"),
        ])
        logger.info("[main] Bot commands menu updated")

    app.post_init = _post_init

    logger.info("[main] Starting polling...")
    app.run_polling(drop_pending_updates=True)


def _register_handlers(app: Application, settings: Settings) -> None:
    from bot.handlers.start import start_handler
    from bot.handlers.help import help_handler
    from bot.handlers.check import check_handler
    from bot.handlers.favorites import favorites_handler, run_favorite_callback

    app.bot_data["settings"] = settings

    app.add_handler(CommandHandler("start", start_handler))
    logger.debug("[main] Registered handler: /start")

    app.add_handler(CommandHandler("help", help_handler))
    logger.debug("[main] Registered handler: /help")

    app.add_handler(check_handler)
    logger.debug("[main] Registered handler: /check (ConversationHandler)")

    app.add_handler(CommandHandler("favorites", favorites_handler))
    logger.debug("[main] Registered handler: /favorites")

    app.add_handler(CallbackQueryHandler(run_favorite_callback, pattern=r"^run_fav:\d+$"))
    logger.debug("[main] Registered callback: run_fav")

    logger.info("[main] All handlers registered")


if __name__ == "__main__":
    main()
