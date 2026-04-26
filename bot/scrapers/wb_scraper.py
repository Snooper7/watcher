import asyncio
import logging
import random
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
    return f"https://www.wildberries.ru/catalog/0/search.aspx?search={urllib.parse.quote_plus(query)}"


def _parse_price(raw: str) -> float:
    """Strip spaces and ₽ from price text, return as float."""
    cleaned = raw.replace("₽", "").replace("₽", "").replace("\xa0", "").replace(" ", "").strip()
    return float(cleaned)


async def _apply_filter(page, filter_text: str) -> bool:
    """Try to click a filter option by text. Returns True if clicked."""
    base_selectors = [
        ".filter__btn",
        ".filter-feature__btn",
        "label",
        ".j-filter-item",
    ]
    for base in base_selectors:
        try:
            loc = page.locator(base).filter(has_text=filter_text)
            count = await loc.count()
            if count > 0:
                await loc.first.click(timeout=3_000)
                logger.debug("[_apply_filter] Clicked %r via selector %r", filter_text, base)
                return True
        except Exception as exc:
            logger.debug("[_apply_filter] selector=%r filter=%r failed: %s", base, filter_text, exc)
    logger.warning("[_apply_filter] Filter not found in page: %r", filter_text)
    return False


async def _parse_card(card, fallback_url: str) -> ScrapedProduct | None:
    """Parse a single product card element into ScrapedProduct. Returns None if price missing."""
    price_el = await card.query_selector(".price__lower-price")
    name_el = await card.query_selector(".product-card__name")
    brand_el = await card.query_selector(".product-card__brand")
    link_el = await card.query_selector(".product-card__link")

    raw_price = await price_el.inner_text() if price_el else None
    if not raw_price:
        return None

    try:
        price = _parse_price(raw_price)
    except (ValueError, AttributeError):
        return None

    raw_name = await name_el.inner_text() if name_el else None
    raw_brand = await brand_el.inner_text() if brand_el else None
    href = await link_el.get_attribute("href") if link_el else None

    clean_name = raw_name.lstrip("/  ").strip() if raw_name else None
    name_parts = [p for p in [raw_brand, clean_name] if p]
    full_name = " / ".join(name_parts) if name_parts else "Товар"

    return ScrapedProduct(
        name=full_name,
        price=price,
        currency="RUB",
        product_url=href or fallback_url,
        platform="wb",
        query="",
        scraped_at=datetime.now(tz=timezone.utc),
    )


async def _find_cheapest(cards, fallback_url: str) -> ScrapedProduct | None:
    """Parse all cards and return the one with the lowest price."""
    best: tuple[float, ScrapedProduct] | None = None
    for card in cards:
        product = await _parse_card(card, fallback_url)
        if product is None or product.price is None:
            continue
        if best is None or product.price < best[0]:
            best = (product.price, product)
    return best[1] if best else None


class WbScraper(BaseScraper):
    platform = "wb"

    async def scrape(self, query: str) -> ScrapedProduct | None:
        search_url = build_search_url(query)
        logger.debug("[WbScraper.scrape] query=%r url=%s", query, search_url)

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
                        await page.wait_for_selector(".product-card-list .product-card", timeout=15_000)
                    except PlaywrightTimeoutError:
                        logger.warning("[WbScraper.scrape] No product cards: query=%r", query)
                        return None

                    cards = await page.query_selector_all(".product-card-list .product-card")
                    logger.debug("[WbScraper.scrape] Found %d card(s)", len(cards))

                    first = cards[0]
                    product = await _parse_card(first, search_url)
                    if product:
                        product.query = query
                    logger.info("[WbScraper.scrape] name=%r price=%s", product and product.name, product and product.price)
                    return product

                finally:
                    await browser.close()

        except (asyncio.TimeoutError, PlaywrightTimeoutError) as exc:
            logger.warning("[WbScraper.scrape] Timeout: query=%r — %s", query, exc)
            return None
        except Exception as exc:
            logger.error("[WbScraper.scrape] Error: query=%r — %s", query, exc, exc_info=True)
            return None

    async def scrape_brand_with_filters(
        self, brand: str, filter_items: list[str]
    ) -> ScrapedProduct | None:
        search_url = build_search_url(brand)
        logger.debug(
            "[WbScraper.scrape_brand_with_filters] brand=%r filters=%r url=%s",
            brand, filter_items, search_url,
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
                        await page.wait_for_selector(".product-card-list .product-card", timeout=15_000)
                    except PlaywrightTimeoutError:
                        logger.warning(
                            "[WbScraper.scrape_brand_with_filters] No products for brand=%r", brand
                        )
                        return None

                    for filter_text in filter_items:
                        applied = await _apply_filter(page, filter_text)
                        if applied:
                            # Wait for product list to update after filter click
                            await asyncio.sleep(2.0)
                            try:
                                await page.wait_for_selector(
                                    ".product-card-list .product-card", timeout=8_000
                                )
                            except PlaywrightTimeoutError:
                                logger.warning(
                                    "[WbScraper.scrape_brand_with_filters] No products after filter %r", filter_text
                                )
                                return None

                    cards = await page.query_selector_all(".product-card-list .product-card")
                    logger.debug(
                        "[WbScraper.scrape_brand_with_filters] %d card(s) after filters", len(cards)
                    )

                    cheapest = await _find_cheapest(cards, search_url)
                    if cheapest:
                        cheapest.query = brand
                        logger.info(
                            "[WbScraper.scrape_brand_with_filters] Cheapest: name=%r price=%s",
                            cheapest.name, cheapest.price,
                        )
                    return cheapest

                finally:
                    await browser.close()

        except (asyncio.TimeoutError, PlaywrightTimeoutError) as exc:
            logger.warning("[WbScraper.scrape_brand_with_filters] Timeout: brand=%r — %s", brand, exc)
            return None
        except Exception as exc:
            logger.error(
                "[WbScraper.scrape_brand_with_filters] Error: brand=%r — %s", brand, exc, exc_info=True
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
            platform=Platform.wb,
            price=scraped.price,
            currency=scraped.currency,
        )
        session.add(record)
        session.flush()

        product = session.get(Product, product_id)
        if product is not None and not product.wb_url:
            product.wb_url = scraped.product_url

        logger.info(
            "[save_price_record] Saved PriceRecord id=%s price=%s", record.id, record.price
        )
        return record
