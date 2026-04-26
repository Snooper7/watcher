import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.database.db import get_favorite_by_id, get_favorites
from bot.scrapers.wb_scraper import WbScraper

logger = logging.getLogger(__name__)

_scraper = WbScraper()


async def favorites_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    favs = get_favorites(telegram_id)

    if not favs:
        await update.message.reply_text(
            "У вас нет сохранённых запросов.\n"
            "Используйте /check для поиска — после результата предложим сохранить."
        )
        return

    buttons = [
        [InlineKeyboardButton(
            f"{fav.brand}" + (f"  |  {fav.filters}" if fav.filters else ""),
            callback_data=f"run_fav:{fav.id}",
        )]
        for fav in favs
    ]
    keyboard = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("Ваши сохранённые запросы:", reply_markup=keyboard)


async def run_favorite_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()

    fav_id = int(q.data.split(":")[1])
    fav = get_favorite_by_id(fav_id)

    if fav is None:
        await q.edit_message_text("❌ Запрос не найден.")
        return

    filter_list = [f.strip() for f in fav.filters.split(",") if f.strip()] if fav.filters else []
    filters_label = fav.filters or "без фильтров"

    logger.debug(
        "[run_favorite] fav_id=%s brand=%r filters=%r", fav_id, fav.brand, filter_list
    )

    await q.edit_message_text(
        f"🔍 Ищу *{fav.brand}* ({filters_label})...",
        parse_mode="Markdown",
    )

    result = await _scraper.scrape_brand_with_filters(fav.brand, filter_list)

    if result is None:
        logger.warning("[run_favorite] No result: fav_id=%s brand=%r", fav_id, fav.brand)
        await q.edit_message_text("😔 Ничего не нашлось по этому запросу.")
        return

    price_str = (
        f"{result.price:,.0f} {result.currency}".replace(",", " ")
        if result.price is not None
        else "цена не найдена"
    )
    text = (
        f"📦 *{result.name}*\n"
        f"💰 {price_str}\n"
        f"🔗 [Открыть на WB]({result.product_url})"
    )
    logger.info("[run_favorite] fav_id=%s name=%r price=%s", fav_id, result.name, result.price)
    await q.edit_message_text(text, parse_mode="Markdown")
