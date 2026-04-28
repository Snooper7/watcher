import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.database.models import Platform
from bot.reporter import format_price_report, send_group_report

logger = logging.getLogger(__name__)


def _make_product(product_id: int, name: str):
    p = MagicMock()
    p.id = product_id
    p.name = name
    return p


def _make_record(platform: Platform, price: float):
    r = MagicMock()
    r.platform = platform
    r.price = price
    return r


def test_format_price_report_empty():
    result = format_price_report([])
    logger.debug("[test] empty result=%r", result)
    assert "Нет данных" in result


def test_format_price_report_ozon_only():
    product = _make_product(1, "Test Product")
    record = _make_record(Platform.ozon, 1299.0)
    result = format_price_report([(product, record)])
    logger.debug("[test] ozon_only result=%r", result)
    assert "Test Product" in result
    assert "Ozon" in result
    assert "1 299" in result
    assert "WB" not in result


def test_format_price_report_wb_only():
    product = _make_product(2, "WB Item")
    record = _make_record(Platform.wb, 799.0)
    result = format_price_report([(product, record)])
    logger.debug("[test] wb_only result=%r", result)
    assert "WB Item" in result
    assert "WB" in result
    assert "799" in result
    assert "Ozon" not in result


def test_format_price_report_both_platforms():
    product = _make_product(3, "Dual Product")
    wb_record = _make_record(Platform.wb, 900.0)
    ozon_record = _make_record(Platform.ozon, 850.0)
    result = format_price_report([(product, wb_record), (product, ozon_record)])
    logger.debug("[test] both_platforms result=%r", result)
    assert "Dual Product" in result
    assert "WB" in result
    assert "Ozon" in result
    assert "900" in result
    assert "850" in result
    # Product name appears only once (grouped)
    assert result.count("Dual Product") == 1


def test_format_price_report_multiple_products():
    p1 = _make_product(1, "Product A")
    p2 = _make_product(2, "Product B")
    r1 = _make_record(Platform.ozon, 500.0)
    r2 = _make_record(Platform.wb, 600.0)
    result = format_price_report([(p1, r1), (p2, r2)])
    logger.debug("[test] multi result=%r", result)
    assert "Product A" in result
    assert "Product B" in result
    assert "Обновлено: 2 товаров" in result


@pytest.mark.asyncio
async def test_send_group_report_calls_send_message():
    settings = MagicMock()
    settings.GROUP_CHAT_ID = "-100123456"

    app = MagicMock()
    app.bot_data = {"settings": settings}
    app.bot.send_message = AsyncMock()

    product = _make_product(1, "Test")
    record = _make_record(Platform.ozon, 999.0)

    with patch("bot.reporter.get_latest_price_records", return_value=[(product, record)]):
        await send_group_report(app)

    app.bot.send_message.assert_called_once()
    call_kwargs = app.bot.send_message.call_args
    assert call_kwargs.kwargs["chat_id"] == "-100123456"
    assert "Test" in call_kwargs.kwargs["text"]
    logger.debug("[test] send_message called with chat_id=%s", call_kwargs.kwargs["chat_id"])


@pytest.mark.asyncio
async def test_send_group_report_handles_send_error():
    settings = MagicMock()
    settings.GROUP_CHAT_ID = "-100999"

    app = MagicMock()
    app.bot_data = {"settings": settings}
    app.bot.send_message = AsyncMock(side_effect=RuntimeError("Telegram error"))

    with patch("bot.reporter.get_latest_price_records", return_value=[]):
        # Should not raise
        await send_group_report(app)

    logger.debug("[test] send error was swallowed without raising")
