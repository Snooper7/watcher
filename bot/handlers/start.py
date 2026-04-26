import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.database.db import get_session
from bot.database.models import User

logger = logging.getLogger(__name__)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.debug(
        "[start_handler] Received /start from user_id=%s username=%s",
        user.id, user.username,
    )

    with get_session() as session:
        existing = session.query(User).filter_by(telegram_id=user.id).first()

        if existing is None:
            new_user = User(telegram_id=user.id, username=user.username)
            session.add(new_user)
            logger.info(
                "[start_handler] New user registered: telegram_id=%s username=%s",
                user.id, user.username,
            )
        else:
            logger.debug(
                "[start_handler] Returning user: telegram_id=%s username=%s",
                user.id, user.username,
            )

    await update.message.reply_text(
        "Привет! Я слежу за ценами на Wildberries и Ozon.\n"
        "Используй /help чтобы узнать доступные команды."
    )
    logger.debug("[start_handler] Welcome message sent to user_id=%s", user.id)
