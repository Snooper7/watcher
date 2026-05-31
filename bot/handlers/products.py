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

from bot.database.db import (
    add_product,
    get_or_create_user,
    get_product_by_id,
    list_products,
    remove_product,
)
from bot.scrapers.factory import make_ozon_scraper
from bot.scrapers.wb_scraper import WbScraper

logger = logging.getLogger(__name__)

WAITING_BRAND, WAITING_URL, WAITING_CONFIRM = range(3)

_wb_scraper = WbScraper()
_ozon_scraper = make_ozon_scraper()


def _detect_platform(text: str) -> tuple[str | None, str | None]:
    """Return (wb_url, ozon_url) — exactly one is set if text is a recognised URL."""
    if "wildberries.ru" in text or "wb.ru" in text:
        return text, None
    if "ozon.ru" in text:
        return None, text
    return None, None


async def _add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.debug("[add_start] telegram_id=%s", update.effective_user.id)
    await update.message.reply_text("Введите название производителя или бренда товара:")
    return WAITING_BRAND


async def _got_brand(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    brand = update.message.text.strip()
    context.user_data["add_brand"] = brand
    logger.debug("[add_got_brand] brand=%r", brand)
    await update.message.reply_text(
        "Отправьте URL товара с WB или Ozon (с применёнными фильтрами из браузера).\n"
        "Или просто введите название товара — будет сохранено без ссылки."
    )
    return WAITING_URL


async def _got_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    brand = context.user_data.get("add_brand", "")
    telegram_id = update.effective_user.id
    username = update.effective_user.username

    if not text:
        await update.message.reply_text("Текст не может быть пустым. Попробуйте ещё раз:")
        return WAITING_URL

    wb_url, ozon_url = _detect_platform(text)

    # Plain name — save immediately, no scraping needed
    if not wb_url and not ozon_url:
        user = get_or_create_user(telegram_id, username)
        product = add_product(user.id, text)
        logger.info("[add_got_url] saved plain product id=%s name=%r", product.id, text)
        await update.message.reply_text(f"✅ Товар «{text}» добавлен в список мониторинга.")
        return ConversationHandler.END

    platform_label = "WB" if wb_url else "Ozon"
    msg = await update.message.reply_text(f"🔍 Ищу товар на {platform_label}...")

    scraper = _ozon_scraper if ozon_url else _wb_scraper
    result = await scraper.scrape_brand_with_filters(brand, [wb_url or ozon_url])

    if result is None:
        logger.warning("[add_got_url] scrape returned None: brand=%r url=%s", brand, text)
        await msg.edit_text(
            "😔 Не удалось найти товар по этой ссылке.\n"
            "Проверьте URL и попробуйте ещё раз:"
        )
        return WAITING_URL

    context.user_data["add_name"] = result.name
    context.user_data["add_wb_url"] = wb_url
    context.user_data["add_ozon_url"] = ozon_url

    price_str = (
        f"{result.price:,.0f} ₽".replace(",", " ")
        if result.price is not None else "—"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Добавить в мониторинг", callback_data="add_prod_yes"),
        InlineKeyboardButton("❌ Отмена", callback_data="add_prod_no"),
    ]])
    await msg.edit_text(
        f"📦 *{result.name}*\n"
        f"💰 {price_str}\n"
        f"🏪 {platform_label}\n\n"
        "Это нужный товар? Добавить в список мониторинга?",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    logger.debug("[add_got_url] awaiting confirmation for name=%r", result.name)
    return WAITING_CONFIRM


async def _confirm_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    name = context.user_data.get("add_name", "Товар")
    wb_url = context.user_data.get("add_wb_url")
    ozon_url = context.user_data.get("add_ozon_url")

    user = get_or_create_user(q.from_user.id, q.from_user.username)
    product = add_product(user.id, name, wb_url, ozon_url)
    logger.info("[confirm_add] saved product id=%s name=%r", product.id, name)
    await q.edit_message_text(f"✅ Товар «{name}» добавлен в список мониторинга.")
    return ConversationHandler.END


async def _reject_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    logger.debug("[reject_add] telegram_id=%s cancelled", q.from_user.id)
    await q.edit_message_text("Добавление отменено. Попробуйте с другим URL командой /add.")
    return ConversationHandler.END


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.debug("[add_cancel] telegram_id=%s", update.effective_user.id)
    await update.message.reply_text("Добавление отменено.")
    return ConversationHandler.END


add_handler = ConversationHandler(
    entry_points=[CommandHandler("add", _add_start)],
    states={
        WAITING_BRAND: [MessageHandler(filters.TEXT & ~filters.COMMAND, _got_brand)],
        WAITING_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, _got_url)],
        WAITING_CONFIRM: [
            CallbackQueryHandler(_confirm_add, pattern="^add_prod_yes$"),
            CallbackQueryHandler(_reject_add, pattern="^add_prod_no$"),
        ],
    },
    fallbacks=[CommandHandler("cancel", _cancel)],
)


async def list_products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    username = update.effective_user.username
    logger.debug("[list_products] telegram_id=%s", telegram_id)

    user = get_or_create_user(telegram_id, username)
    products = list_products(user.id)

    logger.debug("[list_products] telegram_id=%s count=%d", telegram_id, len(products))

    if not products:
        await update.message.reply_text(
            "У вас нет товаров в списке мониторинга.\n"
            "Добавьте товар командой /add"
        )
        return

    lines = ["📋 Ваши товары для мониторинга:\n"]
    for i, p in enumerate(products, 1):
        # Name might be a legacy URL if saved before the scrape-before-save flow
        name = p.name
        if name.startswith("http"):
            if "wildberries" in name or "wb.ru" in name:
                name = "Товар с Wildberries"
            elif "ozon.ru" in name:
                name = "Товар с Ozon"
            else:
                name = "Товар"

        if p.wb_url:
            link_line = f"   🏪 [WB]({p.wb_url})"
        elif p.ozon_url:
            link_line = f"   🏪 [Ozon]({p.ozon_url})"
        else:
            link_line = "   📝 без ссылки"

        lines.append(f"{i}. {name}\n{link_line}")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def remove_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    username = update.effective_user.username
    logger.debug("[remove_handler] telegram_id=%s", telegram_id)

    user = get_or_create_user(telegram_id, username)
    products = list_products(user.id)

    logger.debug("[remove_handler] telegram_id=%s count=%d", telegram_id, len(products))

    if not products:
        await update.message.reply_text("Список мониторинга пуст.")
        return

    # Same URL→name cleanup for the remove menu
    def _display_name(p) -> str:
        if p.name.startswith("http"):
            if "wildberries" in p.name or "wb.ru" in p.name:
                return "Товар с Wildberries"
            if "ozon.ru" in p.name:
                return "Товар с Ozon"
            return "Товар"
        return p.name

    buttons = [
        [InlineKeyboardButton(f"🗑 {_display_name(p)[:40]}", callback_data=f"rm_prod:{p.id}")]
        for p in products
    ]
    await update.message.reply_text(
        "Выберите товар для удаления:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def remove_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()

    product_id = int(q.data.split(":")[1])
    logger.debug("[remove_product_callback] product_id=%s", product_id)

    product = get_product_by_id(product_id)
    if product is None:
        logger.warning("[remove_product_callback] product_id=%s not found", product_id)
        await q.edit_message_text("❌ Товар не найден.")
        return

    name = product.name
    remove_product(product_id)
    logger.info("[remove_product_callback] removed product id=%s name=%r", product_id, name)
    await q.edit_message_text(f"✅ Товар «{name}» удалён из мониторинга.")
