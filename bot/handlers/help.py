import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "📋 Доступные команды:\n\n"
    "/start — начать работу с ботом\n"
    "/add <ссылка или название> — добавить товар в отслеживание\n"
    "/remove <ссылка или название> — удалить товар\n"
    "/list — список отслеживаемых товаров\n"
    "/help — это сообщение"
)


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.debug("[help_handler] Received /help from user_id=%s", user.id)

    await update.message.reply_text(HELP_TEXT)
    logger.debug("[help_handler] Help message sent to user_id=%s", user.id)
