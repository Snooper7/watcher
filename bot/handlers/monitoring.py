import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def monitoring_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    logger.info("[monitoring_handler] Manual run triggered by telegram_id=%s", telegram_id)

    msg = await update.message.reply_text("🔄 Запускаю мониторинг цен...")

    from bot.scheduler import collect_and_report
    await collect_and_report(context.application)

    await msg.edit_text("✅ Мониторинг завершён. Отчёт отправлен в группу.")
    logger.info("[monitoring_handler] Manual run complete for telegram_id=%s", telegram_id)
