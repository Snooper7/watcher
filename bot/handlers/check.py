import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.scrapers.wb_scraper import WbScraper

logger = logging.getLogger(__name__)

_scraper = WbScraper()


async def check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip() if context.args else ""
    user_id = update.effective_user.id

    if not query:
        await update.message.reply_text(
            "Использование: /check <название товара>\n"
            "Пример: /check Nike Air Force 1"
        )
        return

    logger.debug("[check_handler] user_id=%s query=%r", user_id, query)

    msg = await update.message.reply_text(f"🔍 Ищу «{query}» на Wildberries...")

    result = await _scraper.scrape(query)

    if result is None:
        logger.warning("[check_handler] No result for query=%r user_id=%s", query, user_id)
        await msg.edit_text(f"😔 Ничего не нашёл по запросу «{query}».")
        return

    price_str = f"{result.price:,.0f} {result.currency}".replace(",", " ") if result.price else "цена не найдена"

    text = (
        f"📦 *{result.name}*\n"
        f"💰 {price_str}\n"
        f"🔗 [Открыть на WB]({result.product_url})"
    )
    logger.info("[check_handler] Result for user_id=%s: name=%r price=%s", user_id, result.name, result.price)
    await msg.edit_text(text, parse_mode="Markdown")
