import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.scrapers.base import ScrapedProduct

logger = logging.getLogger(__name__)

_SCRAPED_OZON = ScrapedProduct(
    name="Test Ozon",
    price=999.0,
    currency="RUB",
    product_url="https://ozon.ru/product/1",
    platform="ozon",
    query="Test",
    scraped_at=datetime.now(tz=timezone.utc),
)

_SCRAPED_WB = ScrapedProduct(
    name="Test WB",
    price=799.0,
    currency="RUB",
    product_url="https://wildberries.ru/catalog/1",
    platform="wb",
    query="Test",
    scraped_at=datetime.now(tz=timezone.utc),
)


def _make_product(product_id: int, name: str, wb_url=None, ozon_url=None):
    p = MagicMock()
    p.id = product_id
    p.name = name
    p.wb_url = wb_url
    p.ozon_url = ozon_url
    return p


@pytest.mark.asyncio
async def test_collect_prices_calls_ozon_scraper_for_ozon_product():
    ozon_product = _make_product(1, "Ozon Product", ozon_url="https://ozon.ru/product/1")

    with (
        patch("bot.scheduler.list_all_products_with_urls", return_value=[ozon_product]),
        patch("bot.scheduler._ozon_scraper.scrape_brand_with_filters", new=AsyncMock(return_value=_SCRAPED_OZON)) as mock_ozon,
        patch("bot.scheduler._wb_scraper.scrape_brand_with_filters", new=AsyncMock(return_value=None)) as mock_wb,
        patch("bot.scheduler.save_price_record"),
    ):
        from bot.scheduler import collect_prices
        await collect_prices(None)

        mock_ozon.assert_called_once_with("Ozon Product", ["https://ozon.ru/product/1"])
        mock_wb.assert_not_called()
        logger.debug("[test] OzonScraper called, WbScraper not called")


@pytest.mark.asyncio
async def test_collect_prices_calls_wb_scraper_for_wb_product():
    wb_product = _make_product(2, "WB Product", wb_url="https://wildberries.ru/catalog/1")

    with (
        patch("bot.scheduler.list_all_products_with_urls", return_value=[wb_product]),
        patch("bot.scheduler._ozon_scraper.scrape_brand_with_filters", new=AsyncMock(return_value=None)) as mock_ozon,
        patch("bot.scheduler._wb_scraper.scrape_brand_with_filters", new=AsyncMock(return_value=_SCRAPED_WB)) as mock_wb,
        patch("bot.scheduler.save_price_record"),
    ):
        from bot.scheduler import collect_prices
        await collect_prices(None)

        mock_wb.assert_called_once_with("WB Product", ["https://wildberries.ru/catalog/1"])
        mock_ozon.assert_not_called()
        logger.debug("[test] WbScraper called, OzonScraper not called")


@pytest.mark.asyncio
async def test_collect_prices_saves_price_record():
    product = _make_product(3, "Saved Product", ozon_url="https://ozon.ru/product/3")

    with (
        patch("bot.scheduler.list_all_products_with_urls", return_value=[product]),
        patch("bot.scheduler._ozon_scraper.scrape_brand_with_filters", new=AsyncMock(return_value=_SCRAPED_OZON)),
        patch("bot.scheduler.save_price_record") as mock_save,
    ):
        from bot.scheduler import collect_prices
        await collect_prices(None)

        mock_save.assert_called_once_with(3, _SCRAPED_OZON)
        logger.debug("[test] save_price_record called with correct args")


@pytest.mark.asyncio
async def test_collect_prices_continues_on_error():
    good_product = _make_product(4, "Good Product", ozon_url="https://ozon.ru/product/4")
    bad_product = _make_product(5, "Bad Product", ozon_url="https://ozon.ru/product/5")

    async def _scrape_side_effect(name, urls):
        if name == "Bad Product":
            raise RuntimeError("scrape failed")
        return _SCRAPED_OZON

    with (
        patch("bot.scheduler.list_all_products_with_urls", return_value=[bad_product, good_product]),
        patch("bot.scheduler._ozon_scraper.scrape_brand_with_filters", new=AsyncMock(side_effect=_scrape_side_effect)),
        patch("bot.scheduler.save_price_record") as mock_save,
    ):
        from bot.scheduler import collect_prices
        await collect_prices(None)

        mock_save.assert_called_once_with(4, _SCRAPED_OZON)
        logger.debug("[test] Error for bad_product did not stop good_product processing")


@pytest.mark.asyncio
async def test_collect_prices_skips_product_without_url():
    no_url_product = _make_product(6, "No URL", wb_url=None, ozon_url=None)

    with (
        patch("bot.scheduler.list_all_products_with_urls", return_value=[no_url_product]),
        patch("bot.scheduler._ozon_scraper.scrape_brand_with_filters", new=AsyncMock()) as mock_ozon,
        patch("bot.scheduler._wb_scraper.scrape_brand_with_filters", new=AsyncMock()) as mock_wb,
        patch("bot.scheduler.save_price_record") as mock_save,
    ):
        from bot.scheduler import collect_prices
        await collect_prices(None)

        mock_ozon.assert_not_called()
        mock_wb.assert_not_called()
        mock_save.assert_not_called()
        logger.debug("[test] No scraper called for product without URL")
