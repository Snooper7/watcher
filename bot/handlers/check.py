import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.database.db import save_favorite
from bot.scrapers.wb_scraper import WbScraper

logger = logging.getLogger(__name__)

BRAND, FILTERS, SAVE_PROMPT = range(3)

_scraper = WbScraper()

_SAVE_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ Сохранить", callback_data="fav_save"),
    InlineKeyboardButton("❌ Нет", callback_data="fav_skip"),
]])


async def _check_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Введите название производителя:")
    return BRAND


async def _got_brand(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["brand"] = update.message.text.strip()
    await update.message.reply_text(
        "Введите фильтры через запятую или вставьте URL с уже применёнными фильтрами из браузера:\n\n"
        "Текстом: Кроссовки, Размер 42, Белый\n"
        "URL: https://www.wildberries.ru/catalog/0/search.aspx?search=..."
    )
    return FILTERS


async def _got_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    brand = context.user_data.get("brand", "")
    raw = update.message.text.strip()
    filter_list = [f.strip() for f in raw.split(",") if f.strip()]

    user_id = update.effective_user.id
    logger.debug("[check] user_id=%s brand=%r filters=%r", user_id, brand, filter_list)

    filters_label = ", ".join(filter_list) if filter_list else "без фильтров"
    has_url = any(f.startswith("https://") for f in filter_list)
    searching_label = "по заданному URL" if has_url else filters_label

    msg = await update.message.reply_text(
        f"🔍 Ищу *{brand}* ({searching_label})...",
        parse_mode="Markdown",
    )

    result = await _scraper.scrape_brand_with_filters(brand, filter_list)

    if result is None:
        logger.warning("[check] No result: user_id=%s brand=%r filters=%r", user_id, brand, filter_list)
        await msg.edit_text("😔 Ничего не нашлось по заданным фильтрам.")
        return ConversationHandler.END

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
    logger.info("[check] user_id=%s name=%r price=%s", user_id, result.name, result.price)
    context.user_data["filters_str"] = raw

    if result.image_url:
        msg_deleted = False
        try:
            await msg.delete()
            msg_deleted = True
            await update.message.reply_photo(
                photo=result.image_url, caption=text, parse_mode="Markdown"
            )
            await update.message.reply_text("Сохранить этот запрос в избранное?", reply_markup=_SAVE_KB)
            return SAVE_PROMPT
        except Exception as exc:
            logger.warning("[check] Photo send failed: %s", exc)
            if msg_deleted:
                await update.message.reply_text(text, parse_mode="Markdown")
                await update.message.reply_text("Сохранить этот запрос в избранное?", reply_markup=_SAVE_KB)
                return SAVE_PROMPT

    await msg.edit_text(text, parse_mode="Markdown")
    await update.message.reply_text("Сохранить этот запрос в избранное?", reply_markup=_SAVE_KB)
    return SAVE_PROMPT


async def _on_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    brand = context.user_data.get("brand", "")
    filters_str = context.user_data.get("filters_str") or None
    save_favorite(update.effective_user.id, brand, filters_str)
    await q.edit_message_text("✅ Запрос сохранён в избранное.")
    return ConversationHandler.END


async def _on_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("Хорошо, запрос не сохранён.")
    return ConversationHandler.END


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Поиск отменён.")
    return ConversationHandler.END


check_handler = ConversationHandler(
    entry_points=[CommandHandler("check", _check_start)],
    states={
        BRAND: [MessageHandler(filters.TEXT & ~filters.COMMAND, _got_brand)],
        FILTERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, _got_filters)],
        SAVE_PROMPT: [
            CallbackQueryHandler(_on_save, pattern="^fav_save$"),
            CallbackQueryHandler(_on_skip, pattern="^fav_skip$"),
        ],
    },
    fallbacks=[CommandHandler("cancel", _cancel)],
)
