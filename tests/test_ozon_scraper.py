import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from bot.scrapers.ozon_scraper import OzonScraper, _parse_price, build_search_url

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
# Helpers: build a full playwright mock chain
# ---------------------------------------------------------------------------

def _make_playwright_mock(
    tiles: list,
    goto_side_effect=None,
    wait_for_selector_side_effect=None,
):
    """Return (mock_async_playwright_fn, mock_page) with the provided tile list."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock(side_effect=goto_side_effect)
    mock_page.wait_for_selector = AsyncMock(side_effect=wait_for_selector_side_effect)
    mock_page.query_selector_all = AsyncMock(return_value=tiles)

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_p = MagicMock()
    mock_p.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_ctx = AsyncMock()
    mock_pw_ctx.__aenter__ = AsyncMock(return_value=mock_p)
    mock_pw_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_async_playwright = MagicMock(return_value=mock_pw_ctx)
    return mock_async_playwright, mock_page


def _make_tile(price_text: str, name_text: str, href: str) -> AsyncMock:
    """Build a mock Ozon product tile element."""
    tile = AsyncMock()

    link_el = AsyncMock()
    link_el.get_attribute = AsyncMock(return_value=href)

    async def _query_selector(selector: str):
        if "product" in selector:
            return link_el
        return None

    tile.query_selector = _query_selector

    # evaluate() is used for price and name extraction via JS
    async def _evaluate(script: str):
        logger.debug("[mock_tile.evaluate] script excerpt=%r", script[:60])
        if "₽" in script or "price" in script.lower():
            return price_text
        if "title" in script or "href" in script or "innerText" in script:
            return name_text
        if "ozon" in script or "cdn" in script:
            return None
        return None

    tile.evaluate = _evaluate
    return tile


# ---------------------------------------------------------------------------
# OzonScraper.scrape — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_extracts_first_result() -> None:
    tile = _make_tile("49 999 ₽", "Nike Air Max", "/product/nike-air-max-123/")
    mock_pw, _ = _make_playwright_mock(tiles=[tile])

    scraper = OzonScraper()
    with (
        patch("bot.scrapers.ozon_scraper.async_playwright", mock_pw),
        patch("bot.scrapers.ozon_scraper._STEALTH") as mock_stealth,
        patch("bot.scrapers.ozon_scraper.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_stealth.apply_stealth_async = AsyncMock()
        result = await scraper.scrape("Nike Air Max")

    logger.debug("[test_scrape_extracts_first_result] result=%s", result)
    assert result is not None
    assert result.price == 49999.0
    assert result.platform == "ozon"
    assert result.currency == "RUB"


# ---------------------------------------------------------------------------
# OzonScraper.scrape — no results (wait_for_selector timeout)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_returns_none_on_no_results() -> None:
    mock_pw, _ = _make_playwright_mock(
        tiles=[],
        wait_for_selector_side_effect=PlaywrightTimeoutError("timeout"),
    )

    scraper = OzonScraper()
    with (
        patch("bot.scrapers.ozon_scraper.async_playwright", mock_pw),
        patch("bot.scrapers.ozon_scraper._STEALTH") as mock_stealth,
        patch("bot.scrapers.ozon_scraper.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_stealth.apply_stealth_async = AsyncMock()
        result = await scraper.scrape("nonexistent product xyz")

    logger.debug("[test_scrape_returns_none_on_no_results] result=%s", result)
    assert result is None


# ---------------------------------------------------------------------------
# OzonScraper.scrape — navigation timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_returns_none_on_navigation_timeout() -> None:
    mock_pw, _ = _make_playwright_mock(
        tiles=[],
        goto_side_effect=asyncio.TimeoutError("navigation timeout"),
    )

    scraper = OzonScraper()
    with (
        patch("bot.scrapers.ozon_scraper.async_playwright", mock_pw),
        patch("bot.scrapers.ozon_scraper._STEALTH") as mock_stealth,
        patch("bot.scrapers.ozon_scraper.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_stealth.apply_stealth_async = AsyncMock()
        result = await scraper.scrape("any product")

    logger.debug("[test_scrape_returns_none_on_navigation_timeout] result=%s", result)
    assert result is None
