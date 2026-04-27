import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.scrapers.ozon_scraper import (
    OzonScraper, _parse_price, _weight_from_url, _cheapest, build_search_url, ScrapedProduct
)
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# build_search_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected_fragment", [
    ("Nike Air Force 1", "Nike+Air+Force+1"),
    ("Samsung Galaxy S24", "Samsung+Galaxy+S24"),
])
def test_build_search_url(query: str, expected_fragment: str) -> None:
    url = build_search_url(query)
    logger.debug("[test_build_search_url] query=%r url=%s", query, url)
    assert "ozon.ru" in url
    assert expected_fragment in url


# ---------------------------------------------------------------------------
# _parse_price
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("49 999 ₽", 49999.0),
    ("1 234 567 ₽", 1234567.0),
    ("0 ₽", 0.0),
])
def test_parse_price(raw: str, expected: float) -> None:
    logger.debug("[test_parse_price] raw=%r expected=%s", raw, expected)
    assert _parse_price(raw) == expected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_nodriver_mock(cards: list[dict] | None = None, browser_get_side_effect=None):
    """
    Returns (mock_uc_start, mock_tab).

    The scraper calls tab.evaluate() twice:
      1. _WAIT_RESULTS_JS  → expects True (widget found)
      2. _EXTRACT_CARDS_JS → expects list[dict] of card data
    """
    from bot.scrapers.ozon_scraper import _WAIT_RESULTS_JS

    mock_tab = AsyncMock()
    mock_tab.get_content = AsyncMock(return_value="<html></html>")

    call_count = {"n": 0}

    async def _evaluate(js, *args, **kwargs):
        call_count["n"] += 1
        if js == _WAIT_RESULTS_JS:
            logger.debug("[mock_tab.evaluate] call #%d → True (widget found)", call_count["n"])
            return True
        # _EXTRACT_CARDS_JS
        logger.debug("[mock_tab.evaluate] call #%d → cards list", call_count["n"])
        return cards or []

    mock_tab.evaluate = _evaluate

    mock_browser = MagicMock()
    mock_browser.stop = MagicMock()
    if browser_get_side_effect:
        mock_browser.get = AsyncMock(side_effect=browser_get_side_effect)
    else:
        mock_browser.get = AsyncMock(return_value=mock_tab)

    return AsyncMock(return_value=mock_browser), mock_tab


# ---------------------------------------------------------------------------
# OzonScraper.scrape — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_extracts_first_result() -> None:
    cards = [{"price": "49 999 ₽", "productUrl": "https://www.ozon.ru/product/1/", "name": "Nike Air Max", "img": None}]
    mock_start, _ = _make_nodriver_mock(cards=cards)

    scraper = OzonScraper()
    with (
        patch("bot.scrapers.ozon_scraper.uc.start", mock_start),
        patch("bot.scrapers.ozon_scraper.asyncio.sleep", new=AsyncMock()),
        patch("bot.scrapers.ozon_scraper._wait_for_results", new=AsyncMock(return_value=True)),
        patch("bot.scrapers.ozon_scraper._wait_for_prices", new=AsyncMock(return_value=True)),
    ):
        result = await scraper.scrape("Nike Air Max")

    logger.debug("[test_scrape_extracts_first_result] result=%s", result)
    assert result is not None
    assert result.price == 49999.0
    assert result.platform == "ozon"
    assert result.currency == "RUB"
    assert result.name == "Nike Air Max"


# ---------------------------------------------------------------------------
# OzonScraper.scrape — no results widget found
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_returns_none_on_no_results() -> None:
    mock_start, _ = _make_nodriver_mock(cards=None)

    scraper = OzonScraper()
    with (
        patch("bot.scrapers.ozon_scraper.uc.start", mock_start),
        patch("bot.scrapers.ozon_scraper.asyncio.sleep", new=AsyncMock()),
        patch("bot.scrapers.ozon_scraper._wait_for_results", new=AsyncMock(return_value=False)),
        patch("bot.scrapers.ozon_scraper._wait_for_prices", new=AsyncMock(return_value=False)),
    ):
        result = await scraper.scrape("nonexistent product")

    logger.debug("[test_scrape_returns_none_on_no_results] result=%s", result)
    assert result is None


# ---------------------------------------------------------------------------
# OzonScraper.scrape — prices never appear (headless antibot)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_returns_none_when_prices_never_load() -> None:
    mock_start, _ = _make_nodriver_mock(cards=[])

    scraper = OzonScraper()
    with (
        patch("bot.scrapers.ozon_scraper.uc.start", mock_start),
        patch("bot.scrapers.ozon_scraper.asyncio.sleep", new=AsyncMock()),
        patch("bot.scrapers.ozon_scraper._wait_for_results", new=AsyncMock(return_value=True)),
        patch("bot.scrapers.ozon_scraper._wait_for_prices", new=AsyncMock(return_value=False)),
    ):
        result = await scraper.scrape("hidden product")

    logger.debug("[test_scrape_returns_none_when_prices_never_load] result=%s", result)
    assert result is None


# ---------------------------------------------------------------------------
# _weight_from_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://www.ozon.ru/category/foo/?weight=2000.000%3B2000.000", 2000),
    ("https://www.ozon.ru/category/foo/?weight=400.000%3B400.000", 400),
    ("https://www.ozon.ru/search/?text=foo", None),
])
def test_weight_from_url(url, expected):
    assert _weight_from_url(url) == expected


# ---------------------------------------------------------------------------
# _cheapest — weight filtering
# ---------------------------------------------------------------------------

def _make_product(name, price, url="https://www.ozon.ru/product/x/"):
    return ScrapedProduct(
        name=name, price=price, currency="RUB", product_url=url,
        platform="ozon", query="", scraped_at=datetime.now(tz=timezone.utc),
    )


def test_cheapest_prefers_weight_match():
    p400 = _make_product("Корм 400г", 500.0, "https://www.ozon.ru/product/foo-400g/")
    p2000 = _make_product("Корм 2кг", 1800.0, "https://www.ozon.ru/product/foo-2kg/")
    result = _cheapest([p400, p2000], weight_g=2000)
    assert result is p2000


def test_cheapest_falls_back_when_no_weight_match():
    p1 = _make_product("Корм без веса А", 300.0)
    p2 = _make_product("Корм без веса Б", 500.0)
    result = _cheapest([p1, p2], weight_g=2000)
    assert result is p1  # no match → cheapest overall


# ---------------------------------------------------------------------------
# OzonScraper.scrape — navigation error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_returns_none_on_navigation_timeout() -> None:
    mock_start, _ = _make_nodriver_mock(browser_get_side_effect=Exception("navigation timeout"))

    scraper = OzonScraper()
    with (
        patch("bot.scrapers.ozon_scraper.uc.start", mock_start),
        patch("bot.scrapers.ozon_scraper.asyncio.sleep", new=AsyncMock()),
    ):
        result = await scraper.scrape("any product")

    logger.debug("[test_scrape_returns_none_on_navigation_timeout] result=%s", result)
    assert result is None
