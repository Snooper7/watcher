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


class WbScraper(BaseScraper):
    platform = "wb"

    async def scrape(self, query: str) -> ScrapedProduct | None:
        search_url = build_search_url(query)
        logger.debug("[WbScraper.scrape] Starting scrape: query=%r, search_url=%s", query, search_url)

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(user_agent=_USER_AGENT)
                    page = await context.new_page()
                    await _STEALTH.apply_stealth_async(page)

                    delay = random.uniform(1.5, 3.5)
                    logger.debug("[WbScraper.scrape] Random delay before navigation: %.2fs", delay)
                    await asyncio.sleep(delay)

                    await page.goto(search_url, timeout=30_000)
                    logger.debug("[WbScraper.scrape] Page loaded: %s", search_url)

                    try:
                        await page.wait_for_selector(
                            ".product-card-list .product-card",
                            timeout=15_000,
                        )
                    except PlaywrightTimeoutError:
                        logger.warning(
                            "[WbScraper.scrape] No product cards found (empty results): query=%r, url=%s",
                            query, search_url,
                        )
                        return None

                    cards = await page.query_selector_all(".product-card-list .product-card")
                    logger.debug("[WbScraper.scrape] Found %d product card(s)", len(cards))

                    first = cards[0]

                    price_el = await first.query_selector(".price-block__final-price")
                    name_el = await first.query_selector(".product-card__name")
                    link_el = await first.query_selector(".product-card__link")

                    raw_price = await price_el.inner_text() if price_el else None
                    raw_name = await name_el.inner_text() if name_el else None
                    href = await link_el.get_attribute("href") if link_el else None

                    logger.debug(
                        "[WbScraper.scrape] Extracted raw: price=%r, name=%r, href=%r",
                        raw_price, raw_name, href,
                    )

                    price: float | None = None
                    if raw_price:
                        try:
                            price = _parse_price(raw_price)
                        except (ValueError, AttributeError) as exc:
                            logger.warning("[WbScraper.scrape] Price parse failed: %r — %s", raw_price, exc)

                    product_url = href if href else search_url

                    result = ScrapedProduct(
                        name=raw_name or query,
                        price=price,
                        currency="RUB",
                        product_url=product_url,
                        platform=self.platform,
                        query=query,
                        scraped_at=datetime.now(tz=timezone.utc),
                    )
                    logger.info(
                        "[WbScraper.scrape] Success: name=%r, price=%s, product_url=%s",
                        result.name, result.price, result.product_url,
                    )
                    return result

                finally:
                    await browser.close()

        except (asyncio.TimeoutError, PlaywrightTimeoutError) as exc:
            logger.warning(
                "[WbScraper.scrape] Timeout: query=%r, url=%s — %s",
                query, search_url, exc,
            )
            return None
        except Exception as exc:
            logger.error(
                "[WbScraper.scrape] Unexpected error: query=%r — %s",
                query, exc, exc_info=True,
            )
            return None


def save_price_record(product_id: int, scraped: ScrapedProduct) -> PriceRecord:
    logger.debug(
        "[save_price_record] Saving: product_id=%d, price=%s, platform=%s",
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
            old_url = product.wb_url
            product.wb_url = scraped.product_url
            logger.debug(
                "[save_price_record] Updated wb_url: %r -> %r",
                old_url, scraped.product_url,
            )

        logger.info(
            "[save_price_record] Saved PriceRecord id=%s, price=%s, checked_at=%s",
            record.id, record.price, record.checked_at,
        )
        return record
