"""
Diagnostic script for the Ozon scraper (nodriver).

Usage:
    # full run (visible + headless + scraper + weight correction):
    python tools/test_ozon_debug.py

    # only the weight-correction check (fastest, ~30-40 s):
    python tools/test_ozon_debug.py --weight-only
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nodriver as uc
from nodriver import cdp
from bot.scrapers.ozon_scraper import (
    _EXTRACT_CARDS_JS,
    _EXTRACT_VARIANTS_JS,
    _STEALTH_JS,
    _unwrap_cdp,
    _parse_variants_json,
    _resolve_weight_price,
    _weight_from_url,
    OzonScraper,
)

os.makedirs("logs", exist_ok=True)
_log_path = "logs/ozon_debug_run.log"
_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(
    level=logging.DEBUG,
    format=_fmt,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_log_path, mode="w", encoding="utf-8"),
    ],
)
# Reduce noise from nodriver internals
logging.getLogger("uc").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("ozon_debug").info("Log file: %s", os.path.abspath(_log_path))

logger = logging.getLogger("ozon_debug")

TEST_URL = (
    "https://www.ozon.ru/category/suhie-korma-dlya-koshek-12349/grandorf-fresh-101167374/"
    "?ages=51290&deny_category_prediction=true&from_global=true&maintastetype=118328"
    "&recommendations=51304&text=GRANDORF+FRESH&weight=2000.000%3B2000.000"
)

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


def _make_browser_args(headless: bool) -> list[str]:
    args = [
        "--window-size=1920,1080",
        "--lang=ru-RU",
        "--disable-blink-features=AutomationControlled",
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    ]
    if headless:
        args.insert(0, "--headless=new")
    return args


async def run_visible():
    logger.info("=== VISIBLE MODE ===")
    browser = await uc.start(
        browser_args=_make_browser_args(headless=False),
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
        browser_args=_make_browser_args(headless=True),
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
    if isinstance(items, list):
        from bot.scrapers.ozon_scraper import _matches_weight, ScrapedProduct
        from datetime import datetime, timezone
        weight_g = _weight_from_url(TEST_URL)
        logger.info("Weight filter from URL: %s g", weight_g)
        for i, item in enumerate(items):
            p = ScrapedProduct(
                name=item.get("name") or "",
                price=0,
                currency="RUB",
                product_url=item.get("productUrl") or "",
                platform="ozon",
                query="",
                scraped_at=datetime.now(tz=timezone.utc),
            )
            match = _matches_weight(p, weight_g) if weight_g else "n/a"
            logger.info(
                "  [%02d] price=%-12s weight_match=%-5s name=%s",
                i, item.get("price"), match, (item.get("name") or "")[:60],
            )

    browser.stop()


async def run_weight_correction():
    """
    Tests _resolve_weight_price directly.

    Steps:
    1. Run a headless search with the weight-filtered URL to find a product URL.
    2. Navigate to that product page with _resolve_weight_price.
    3. Log what variants were found and what price was returned.
    4. Compare with the card price (should be different if the card showed min-weight price).
    """
    logger.info("=== WEIGHT CORRECTION CHECK ===")
    weight_g = _weight_from_url(TEST_URL)
    logger.info("Requested weight: %s g", weight_g)

    browser = await uc.start(
        browser_args=_make_browser_args(headless=True),
        lang="ru-RU",
    )
    try:
        tab = await browser.get("about:blank")
        await tab.send(cdp.page.add_script_to_evaluate_on_new_document(_STEALTH_JS))
        await tab.get(TEST_URL)
        await asyncio.sleep(4)

        for scroll_y in (600, 1200, 1800):
            await tab.evaluate(f"window.scrollTo(0, {scroll_y})")
            await asyncio.sleep(1.2)
        await tab.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(1.0)

        # Extract the first card to get a product URL
        raw = await tab.evaluate(_EXTRACT_CARDS_JS)
        items = _unwrap_cdp(raw)
        if isinstance(items, list):
            items = [_unwrap_cdp(i) for i in items]

        if not items:
            logger.error("No cards extracted from search page — check headless detection")
            return

        first = items[0]
        card_price = first.get("price")
        product_url = first.get("productUrl")
        logger.info("First card  — name=%r  card_price=%r  url=%s",
                    (first.get("name") or "")[:60], card_price, product_url)

        if not product_url:
            logger.error("No product URL in first card")
            return

        # Now test _resolve_weight_price on the same tab
        corrected = await _resolve_weight_price(tab, product_url, weight_g)
        logger.info("Card price     : %s", card_price)
        logger.info("Corrected price: %s", corrected)

        if corrected is None:
            logger.warning(
                "FAIL — _resolve_weight_price returned None; check logs/ozon_product_*.html"
            )
        else:
            try:
                from bot.scrapers.ozon_scraper import _parse_price
                card_float = _parse_price(card_price) if card_price else None
            except Exception:
                card_float = None
            if card_float is not None and abs(corrected - card_float) > 0.01:
                logger.info(
                    "OK — prices differ: card=%.2f → corrected=%.2f (correction worked)",
                    card_float, corrected,
                )
            else:
                logger.info(
                    "Prices are the same (%.2f) — card was already the right weight variant",
                    corrected,
                )

    finally:
        browser.stop()


async def run_scraper():
    logger.info("=== OzonScraper.scrape_brand_with_filters() ===")
    scraper = OzonScraper()
    result = await scraper.scrape_brand_with_filters("GRANDORF FRESH", [TEST_URL])
    logger.info("Scraper result: %s", result)
    if result:
        logger.info("  name  : %s", result.name)
        logger.info("  price : %s %s", result.price, result.currency)
        logger.info("  url   : %s", result.product_url)


async def main(weight_only: bool = False):
    if weight_only:
        await run_weight_correction()
        return
    for name, coro in [
        ("run_visible", run_visible()),
        ("run_headless", run_headless()),
        ("run_weight_correction", run_weight_correction()),
        ("run_scraper", run_scraper()),
    ]:
        try:
            await coro
        except Exception as exc:
            logger.error("=== %s CRASHED: %s ===", name, exc, exc_info=True)
        await asyncio.sleep(2)


if __name__ == "__main__":
    weight_only = "--weight-only" in sys.argv
    asyncio.run(main(weight_only=weight_only))
