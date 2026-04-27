"""
Diagnostic script for the Ozon scraper (nodriver).

Usage:
    python tools/test_ozon_debug.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nodriver as uc
from nodriver import cdp
from bot.scrapers.ozon_scraper import _EXTRACT_CARDS_JS, _STEALTH_JS, _unwrap_cdp, OzonScraper

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("ozon_debug")

TEST_URL = (
    "https://www.ozon.ru/category/suhie-korma-dlya-koshek-12349/grandorf-fresh-101167374/"
    "?ages=51290&deny_category_prediction=true&from_global=true&maintastetype=118328"
    "&recommendations=51304&text=GRANDORF+FRESH&weight=2000.000%3B2000.000"
)

# Check whether at least one price span is present inside product tiles
PRICE_PRESENT_JS = (
    "Array.from(document.querySelectorAll('[data-index] span')).some(s => "
    "/\\d[\\d\\s\\u00a0\\u2009]*[₽﹩]/.test(s.innerText || s.textContent || ''))"
)


async def _dump(tab, name: str) -> None:
    os.makedirs("logs", exist_ok=True)
    html = await tab.get_content()
    path = f"logs/debug_{name}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("Dumped %s (%d bytes)", path, len(html))


async def run_visible():
    logger.info("=== VISIBLE MODE ===")
    browser = await uc.start(
        browser_args=[
            "--window-size=1920,1080",
            "--lang=ru-RU",
            "--disable-blink-features=AutomationControlled",
        ],
        lang="ru-RU",
    )

    tab = await browser.get(TEST_URL)
    await asyncio.sleep(4)
    await tab.evaluate("window.scrollBy(0, 800)")
    await asyncio.sleep(1.5)

    title = await tab.evaluate("document.title")
    logger.info("Title: %s", title)

    tile_count = await tab.evaluate("document.querySelectorAll('[data-index]').length")
    logger.info("Tiles [data-index]: %s", tile_count)

    prices_present = await tab.evaluate(PRICE_PRESENT_JS)
    logger.info("Prices present: %s", prices_present)

    raw = await tab.evaluate(_EXTRACT_CARDS_JS)
    items = _unwrap_cdp(raw)
    if isinstance(items, list):
        items = [_unwrap_cdp(i) for i in items]
    count = len(items) if isinstance(items, list) else f"type={type(items)}"
    logger.info("Extracted cards: %s", count)
    if isinstance(items, list) and items:
        logger.info("First card: %s", items[0])

    browser.stop()


async def run_headless():
    logger.info("=== HEADLESS MODE (--headless=new) ===")
    browser = await uc.start(
        browser_args=[
            "--headless=new",
            "--window-size=1920,1080",
            "--lang=ru-RU",
            "--disable-blink-features=AutomationControlled",
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        ],
        lang="ru-RU",
    )

    tab = await browser.get("about:blank")
    await tab.send(cdp.page.add_script_to_evaluate_on_new_document(_STEALTH_JS))
    await tab.get(TEST_URL)
    await asyncio.sleep(4)

    title = await tab.evaluate("document.title")
    logger.info("Title: %s", title)

    tile_count_0 = await tab.evaluate("document.querySelectorAll('[data-index]').length")
    prices_0 = await tab.evaluate(PRICE_PRESENT_JS)
    logger.info("Before scroll      — tiles: %s, prices: %s", tile_count_0, prices_0)

    await tab.evaluate("window.scrollBy(0, 800)")
    await asyncio.sleep(2.0)
    tile_count_1 = await tab.evaluate("document.querySelectorAll('[data-index]').length")
    prices_1 = await tab.evaluate(PRICE_PRESENT_JS)
    logger.info("After scroll  800  — tiles: %s, prices: %s", tile_count_1, prices_1)

    await tab.evaluate("window.scrollBy(0, 1600)")
    await asyncio.sleep(2.0)
    tile_count_2 = await tab.evaluate("document.querySelectorAll('[data-index]').length")
    prices_2 = await tab.evaluate(PRICE_PRESENT_JS)
    logger.info("After scroll 2400  — tiles: %s, prices: %s", tile_count_2, prices_2)

    # Simulate real user: scroll back up then down
    await tab.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(1.0)
    await tab.evaluate("window.scrollBy(0, 600)")
    await asyncio.sleep(2.0)
    tile_count_3 = await tab.evaluate("document.querySelectorAll('[data-index]').length")
    prices_3 = await tab.evaluate(PRICE_PRESENT_JS)
    logger.info("After up+down      — tiles: %s, prices: %s", tile_count_3, prices_3)

    await _dump(tab, "headless_state")

    raw = await tab.evaluate(_EXTRACT_CARDS_JS)
    items = _unwrap_cdp(raw)
    if isinstance(items, list):
        items = [_unwrap_cdp(i) for i in items]
    count = len(items) if isinstance(items, list) else f"type={type(items)}"
    logger.info("Extracted cards: %s", count)
    if isinstance(items, list) and items:
        logger.info("First card: %s", items[0])

    browser.stop()


async def run_scraper():
    logger.info("=== OzonScraper.scrape_brand_with_filters() ===")
    scraper = OzonScraper()
    result = await scraper.scrape_brand_with_filters("GRANDORF FRESH", [TEST_URL])
    logger.info("Scraper result: %s", result)


async def main():
    await run_visible()
    await asyncio.sleep(2)
    await run_headless()
    await asyncio.sleep(2)
    await run_scraper()


if __name__ == "__main__":
    asyncio.run(main())
