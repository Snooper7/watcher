import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from bot.scrapers.wb_scraper import WbScraper, _parse_price, build_search_url

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# build_search_url
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected_fragment", [
    ("Nike Air Force 1", "Nike+Air+Force+1"),
    ("Samsung Galaxy S24 Samsung", "Samsung+Galaxy+S24"),
])
def test_build_search_url(query: str, expected_fragment: str) -> None:
    url = build_search_url(query)
    logger.debug("[test_build_search_url] query=%r url=%s", query, url)
    assert "wildberries.ru" in url
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
    cards: list,
    goto_side_effect=None,
    wait_for_selector_side_effect=None,
):
    """Return (mock_async_playwright_fn, mock_page) with the provided card list."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock(side_effect=goto_side_effect)
    mock_page.wait_for_selector = AsyncMock(side_effect=wait_for_selector_side_effect)
    mock_page.query_selector_all = AsyncMock(return_value=cards)

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


def _make_card(price_text: str, name_text: str, href: str, brand_text: str = "") -> AsyncMock:
    """Build a mock product card element."""
    price_el = AsyncMock()
    price_el.inner_text = AsyncMock(return_value=price_text)

    name_el = AsyncMock()
    name_el.inner_text = AsyncMock(return_value=name_text)

    brand_el = AsyncMock() if brand_text else None
    if brand_el:
        brand_el.inner_text = AsyncMock(return_value=brand_text)

    link_el = AsyncMock()
    link_el.get_attribute = AsyncMock(return_value=href)

    async def _query_selector(selector: str):
        mapping = {
            ".price__lower-price": price_el,
            ".product-card__name": name_el,
            ".product-card__brand": brand_el,
            ".product-card__link": link_el,
        }
        logger.debug("[mock_card.query_selector] selector=%r", selector)
        return mapping.get(selector)

    card = AsyncMock()
    card.query_selector = _query_selector
    return card


# ---------------------------------------------------------------------------
# WbScraper.scrape — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_extracts_first_result() -> None:
    card = _make_card("49 999 ₽", "Nike Air Max", "https://www.wildberries.ru/catalog/1/detail.aspx")
    mock_pw, _ = _make_playwright_mock(cards=[card])

    scraper = WbScraper()
    with (
        patch("bot.scrapers.wb_scraper.async_playwright", mock_pw),
        patch("bot.scrapers.wb_scraper._STEALTH") as mock_stealth,
        patch("bot.scrapers.wb_scraper.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_stealth.apply_stealth_async = AsyncMock()
        result = await scraper.scrape("Nike Air Max")

    logger.debug("[test_scrape_extracts_first_result] result=%s", result)
    assert result is not None
    assert result.price == 49999.0
    assert result.name == "Nike Air Max"
    assert result.platform == "wb"
    assert result.currency == "RUB"


# ---------------------------------------------------------------------------
# WbScraper.scrape — empty results (wait_for_selector timeout)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_returns_none_on_no_results() -> None:
    mock_pw, _ = _make_playwright_mock(
        cards=[],
        wait_for_selector_side_effect=PlaywrightTimeoutError("timeout"),
    )

    scraper = WbScraper()
    with (
        patch("bot.scrapers.wb_scraper.async_playwright", mock_pw),
        patch("bot.scrapers.wb_scraper._STEALTH") as mock_stealth,
        patch("bot.scrapers.wb_scraper.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_stealth.apply_stealth_async = AsyncMock()
        result = await scraper.scrape("nonexistent product xyz")

    logger.debug("[test_scrape_returns_none_on_no_results] result=%s", result)
    assert result is None


# ---------------------------------------------------------------------------
# WbScraper.scrape — navigation timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_returns_none_on_navigation_timeout() -> None:
    mock_pw, _ = _make_playwright_mock(
        cards=[],
        goto_side_effect=asyncio.TimeoutError("navigation timeout"),
    )

    scraper = WbScraper()
    with (
        patch("bot.scrapers.wb_scraper.async_playwright", mock_pw),
        patch("bot.scrapers.wb_scraper._STEALTH") as mock_stealth,
        patch("bot.scrapers.wb_scraper.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_stealth.apply_stealth_async = AsyncMock()
        result = await scraper.scrape("any product")

    logger.debug("[test_scrape_returns_none_on_navigation_timeout] result=%s", result)
    assert result is None
