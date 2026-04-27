import asyncio
import logging
import os
import random
import re
import urllib.parse
from datetime import datetime, timezone

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright
from playwright_stealth import Stealth

from bot.database.db import get_session
from bot.database.models import Platform, PriceRecord, Product
from bot.scrapers.base import BaseScraper, ScrapedProduct

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_STEALTH = Stealth()


def build_search_url(query: str) -> str:
    return f"https://www.ozon.ru/search/?text={urllib.parse.quote_plus(query)}&from_global=true"


def _parse_price(raw: str) -> float:
    """Strip spaces, ₽ and thousands separators from price text, return as float."""
    cleaned = (
        raw.replace("₽", "")
        .replace("₽", "")
        .replace("\xa0", "")
        .replace(" ", "")
        .replace(" ", "")
        .replace(" ", "")
        .strip()
    )
    # Remove remaining non-numeric characters except dot/comma
    cleaned = re.sub(r"[^\d.,]", "", cleaned).replace(",", ".")
    return float(cleaned)


async def _dump_html(page, name: str) -> None:
    """Save page HTML to logs/ for selector debugging."""
    try:
        os.makedirs("logs", exist_ok=True)
        html = await page.content()
        path = f"logs/debug_{name}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.debug("[_dump_html] Saved %s (%d bytes)", path, len(html))
    except Exception as exc:
        logger.debug("[_dump_html] Failed: %s", exc)


async def _parse_card(tile, fallback_url: str) -> ScrapedProduct | None:
    """Parse a single Ozon product tile into ScrapedProduct. Returns None if price missing."""
    # Product link
    link_el = await tile.query_selector("a[href*='/product/']")
    href = await link_el.get_attribute("href") if link_el else None
    if href and href.startswith("/"):
        product_url = f"https://www.ozon.ru{href}"
    else:
        product_url = href or fallback_url

    # Price: find first span whose text matches a price pattern (digits + ₽)
    raw_price: str | None = await tile.evaluate(
        """el => {
            const spans = el.querySelectorAll('span');
            for (const s of spans) {
                const t = s.innerText || s.textContent || '';
                if (/\\d[\\d\\s\\u00a0\\u2009]*[₽₽]/.test(t)) return t.trim();
            }
            return null;
        }"""
    )
    logger.debug("[_parse_card] raw_price=%r href=%r", raw_price, href)

    if not raw_price:
        return None

    try:
        price = _parse_price(raw_price)
    except (ValueError, AttributeError):
        logger.debug("[_parse_card] Failed to parse price from %r", raw_price)
        return None

    # Product name: text of the product link or nearest heading
    raw_name: str | None = await tile.evaluate(
        """el => {
            const a = el.querySelector('a[href*="/product/"]');
            if (!a) return null;
            return a.getAttribute('title') || a.innerText || a.textContent || null;
        }"""
    )
    logger.debug("[_parse_card] raw_name=%r", raw_name)
    name = (raw_name or "Товар").strip() or "Товар"

    # Image URL: first img with ozon/cdn domain
    image_url: str | None = await tile.evaluate(
        """el => {
            const imgs = el.querySelectorAll('img');
            for (const img of imgs) {
                const src = img.getAttribute('src') || img.getAttribute('data-src') || '';
                if (src && (src.includes('ozon.ru') || src.includes('ozonusercontent') || src.includes('cdn'))) {
                    return src;
                }
            }
            return null;
        }"""
    )

    return ScrapedProduct(
        name=name,
        price=price,
        currency="RUB",
        product_url=product_url,
        platform="ozon",
        query="",
        scraped_at=datetime.now(tz=timezone.utc),
        image_url=image_url,
    )


async def _find_cheapest(tiles, fallback_url: str) -> ScrapedProduct | None:
    """Parse all tiles and return the one with the lowest price."""
    best: tuple[float, ScrapedProduct] | None = None
    for tile in tiles:
        product = await _parse_card(tile, fallback_url)
        if product is None or product.price is None:
            continue
        if best is None or product.price < best[0]:
            best = (product.price, product)
    return best[1] if best else None


async def _apply_filter(page, filter_text: str) -> bool:
    """Try to click a filter option on Ozon by text. Returns True if clicked."""
    scoped_selectors = [
        "[data-widget='catalogFilters'] button",
        "[data-widget='searchFilters'] button",
        "[data-widget='catalogHorizontalFilters'] button",
        "[data-widget='catalogFilters'] label",
        "[data-widget='searchFilters'] label",
    ]
    for selector in scoped_selectors:
        try:
            loc = page.locator(selector).filter(has_text=filter_text)
            count = await loc.count()
            if count > 0:
                await loc.first.click(timeout=3_000)
                logger.debug("[_apply_filter] Clicked %r via %r", filter_text, selector)
                return True
        except Exception as exc:
            logger.debug("[_apply_filter] selector=%r filter=%r: %s", selector, filter_text, exc)

    logger.warning("[_apply_filter] Filter not found: %r", filter_text)
    await _dump_html(page, "ozon_filter")
    return False


_VARIANT_SELECTORS = [
    "[data-widget='webGallery'] button",
    "[data-widget='webCharacteristics'] button",
    ".tsBodyControl500Medium",
    "button[data-widget]",
]

_PRODUCT_PRICE_SELECTORS = [
    "[data-widget='webPrice'] span",
    "[data-widget='webSale'] span",
    ".price-block__final-price",
]


async def _resolve_variant(
    page, product_url: str, filter_items: list[str]
) -> tuple[str, float | None, str | None]:
    """Navigate to product page, try to select variant, extract price and image."""
    try:
        await page.goto(product_url, timeout=20_000)
        await page.wait_for_load_state("networkidle", timeout=15_000)
        await page.evaluate("window.scrollBy(0, 600)")
        await asyncio.sleep(1.5)
    except Exception as exc:
        logger.warning("[_resolve_variant] Failed to load %s: %s", product_url, exc)
        return product_url, None, None

    await _dump_html(page, "ozon_variant")

    for filter_text in filter_items:
        for selector in _VARIANT_SELECTORS:
            try:
                item = page.locator(selector).filter(has_text=filter_text)
                if await item.count() == 0:
                    continue
                await item.first.click(timeout=3_000)
                await asyncio.sleep(1.5)
                logger.debug("[_resolve_variant] Clicked variant %r via %r", filter_text, selector)

                for price_sel in _PRODUCT_PRICE_SELECTORS:
                    price_el = await page.query_selector(price_sel)
                    if not price_el:
                        continue
                    raw = await price_el.inner_text()
                    try:
                        price = _parse_price(raw)
                        image_url = await _page_image_url(page)
                        logger.debug("[_resolve_variant] price=%s image=%s", price, image_url)
                        return page.url, price, image_url
                    except (ValueError, AttributeError):
                        continue
                image_url = await _page_image_url(page)
                return page.url, None, image_url
            except Exception as exc:
                logger.debug("[_resolve_variant] selector=%r filter=%r: %s", selector, filter_text, exc)

    logger.warning("[_resolve_variant] No matching variant for filters: %r", filter_items)
    image_url = await _page_image_url(page)
    return product_url, None, image_url


async def _page_image_url(page) -> str | None:
    """Return the first Ozon CDN image URL found on the current page."""
    try:
        imgs = await page.query_selector_all("img")
        for img in imgs:
            for attr in ("src", "data-src"):
                src = await img.get_attribute(attr)
                if src and ("ozon.ru" in src or "ozonusercontent" in src):
                    logger.debug("[_page_image_url] Found: %s", src)
                    return src
    except Exception as exc:
        logger.debug("[_page_image_url] Failed: %s", exc)
    return None


class OzonScraper(BaseScraper):
    platform = "ozon"

    async def scrape(self, query: str) -> ScrapedProduct | None:
        search_url = build_search_url(query)
        logger.debug("[OzonScraper.scrape] query=%r url=%s", query, search_url)

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(user_agent=_USER_AGENT)
                    page = await context.new_page()
                    await _STEALTH.apply_stealth_async(page)

                    await asyncio.sleep(random.uniform(1.5, 3.5))
                    await page.goto(search_url, timeout=30_000)

                    try:
                        await page.wait_for_selector(
                            '[data-widget="searchResultsV2"]', timeout=15_000
                        )
                    except PlaywrightTimeoutError:
                        logger.warning("[OzonScraper.scrape] No results container: query=%r", query)
                        await _dump_html(page, "ozon_search")
                        return None

                    tiles = await page.query_selector_all(
                        '[data-widget="searchResultsV2"] [data-index]'
                    )
                    logger.debug("[OzonScraper.scrape] Found %d tile(s)", len(tiles))

                    if not tiles:
                        logger.warning("[OzonScraper.scrape] Empty tiles: query=%r", query)
                        await _dump_html(page, "ozon_search_empty")
                        return None

                    product = await _parse_card(tiles[0], search_url)
                    if product:
                        product.query = query
                    logger.info(
                        "[OzonScraper.scrape] name=%r price=%s",
                        product and product.name,
                        product and product.price,
                    )
                    return product

                finally:
                    await browser.close()

        except (asyncio.TimeoutError, PlaywrightTimeoutError) as exc:
            logger.warning("[OzonScraper.scrape] Timeout: query=%r — %s", query, exc)
            return None
        except Exception as exc:
            logger.error("[OzonScraper.scrape] Error: query=%r — %s", query, exc, exc_info=True)
            return None

    async def scrape_brand_with_filters(
        self, brand: str, filter_items: list[str]
    ) -> ScrapedProduct | None:
        direct_url = next(
            (f.strip() for f in filter_items if f.strip().startswith("https://www.ozon.ru")),
            None,
        )
        search_url = direct_url if direct_url else build_search_url(brand)
        filters_to_apply = [] if direct_url else filter_items

        logger.debug(
            "[OzonScraper.scrape_brand_with_filters] brand=%r direct_url=%r filters=%r url=%s",
            brand, bool(direct_url), filter_items, search_url,
        )

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(user_agent=_USER_AGENT)
                    page = await context.new_page()
                    await _STEALTH.apply_stealth_async(page)

                    await asyncio.sleep(random.uniform(1.5, 3.5))
                    await page.goto(search_url, timeout=30_000)

                    try:
                        await page.wait_for_selector(
                            '[data-widget="searchResultsV2"]', timeout=15_000
                        )
                    except PlaywrightTimeoutError:
                        logger.warning(
                            "[OzonScraper.scrape_brand_with_filters] No products for brand=%r", brand
                        )
                        return None

                    for filter_text in filters_to_apply:
                        applied = await _apply_filter(page, filter_text)
                        if applied:
                            await asyncio.sleep(2.0)
                            try:
                                await page.wait_for_selector(
                                    '[data-widget="searchResultsV2"]', timeout=8_000
                                )
                            except PlaywrightTimeoutError:
                                logger.warning(
                                    "[OzonScraper.scrape_brand_with_filters] No products after filter %r",
                                    filter_text,
                                )
                                return None

                    tiles = await page.query_selector_all(
                        '[data-widget="searchResultsV2"] [data-index]'
                    )
                    logger.debug(
                        "[OzonScraper.scrape_brand_with_filters] %d tile(s) after filters", len(tiles)
                    )

                    cheapest = await _find_cheapest(tiles, search_url)
                    if cheapest is None:
                        return None

                    if filter_items:
                        resolved_url, resolved_price, resolved_image = await _resolve_variant(
                            page, cheapest.product_url, filter_items
                        )
                        cheapest.product_url = resolved_url
                        if resolved_price is not None:
                            cheapest.price = resolved_price
                        cheapest.image_url = resolved_image

                    cheapest.query = brand
                    logger.info(
                        "[OzonScraper.scrape_brand_with_filters] Cheapest: name=%r price=%s url=%s",
                        cheapest.name, cheapest.price, cheapest.product_url,
                    )
                    return cheapest

                finally:
                    await browser.close()

        except (asyncio.TimeoutError, PlaywrightTimeoutError) as exc:
            logger.warning(
                "[OzonScraper.scrape_brand_with_filters] Timeout: brand=%r — %s", brand, exc
            )
            return None
        except Exception as exc:
            logger.error(
                "[OzonScraper.scrape_brand_with_filters] Error: brand=%r — %s",
                brand, exc, exc_info=True,
            )
            return None


def save_price_record(product_id: int, scraped: ScrapedProduct) -> PriceRecord:
    logger.debug(
        "[save_price_record] product_id=%d price=%s platform=%s",
        product_id, scraped.price, scraped.platform,
    )
    with get_session() as session:
        record = PriceRecord(
            product_id=product_id,
            platform=Platform.ozon,
            price=scraped.price,
            currency=scraped.currency,
        )
        session.add(record)
        session.flush()

        product = session.get(Product, product_id)
        if product is not None and not product.ozon_url:
            old_url = product.ozon_url
            product.ozon_url = scraped.product_url
            logger.debug(
                "[save_price_record] Updated ozon_url: %r → %r", old_url, scraped.product_url
            )

        logger.info(
            "[save_price_record] Saved PriceRecord id=%s price=%s", record.id, record.price
        )
        return record
