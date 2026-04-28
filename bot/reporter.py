import logging
from datetime import datetime, timezone

from telegram.ext import Application

from bot.database.db import get_latest_price_records
from bot.database.models import Platform, PriceRecord, Product

logger = logging.getLogger(__name__)


def _fmt_price(price: float) -> str:
    return f"{price:,.0f} ₽".replace(",", " ").replace("\xa0", " ")


def format_price_report(rows: list[tuple[Product, PriceRecord]]) -> str:
    if not rows:
        logger.debug("[format_price_report] No rows — returning empty report")
        return "\U0001f4ca Нет данных для отчёта."

    now = datetime.now(tz=timezone.utc).strftime("%d.%m.%Y %H:%M")

    grouped: dict[int, dict] = {}
    for product, record in rows:
        if product.id not in grouped:
            grouped[product.id] = {"product": product, "wb": None, "ozon": None}
        if record.platform == Platform.wb:
            grouped[product.id]["wb"] = record.price
        else:
            grouped[product.id]["ozon"] = record.price

    logger.debug("[format_price_report] products=%d", len(grouped))

    lines = [f"\U0001f4ca Отчёт о ценах — {now}\n"]
    for i, entry in enumerate(grouped.values(), 1):
        product = entry["product"]
        lines.append(f"{i}. *{product.name}*")
        if entry["wb"] is not None:
            lines.append(f"   \U0001f6d2 WB: {_fmt_price(entry['wb'])}")
        if entry["ozon"] is not None:
            lines.append(f"   \U0001f6cd Ozon: {_fmt_price(entry['ozon'])}")

    lines.append(f"\nОбновлено: {len(grouped)} товаров")
    return "\n".join(lines)


async def send_group_report(app: Application) -> None:
    settings = app.bot_data.get("settings")
    if settings is None:
        logger.error("[send_group_report] settings not found in bot_data")
        return

    group_chat_id = settings.GROUP_CHAT_ID
    logger.debug("[send_group_report] Fetching latest price records")

    rows = get_latest_price_records()
    logger.debug("[send_group_report] rows=%d", len(rows))

    text = format_price_report(rows)

    try:
        await app.bot.send_message(
            chat_id=group_chat_id,
            text=text,
            parse_mode="Markdown",
        )
        logger.info(
            "[send_group_report] Sent report to group_chat_id=%s len=%d",
            group_chat_id, len(text),
        )
    except Exception as exc:
        logger.error("[send_group_report] Failed: %s", exc, exc_info=True)
