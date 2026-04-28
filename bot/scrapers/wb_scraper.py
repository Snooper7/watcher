import asyncio
import logging
import os
import random
import re
import urllib.parse
from datetime import datetime, timezone

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright
from playwright_stealth import Stealth

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


async def _apply_filter(page, filter_text: str) -> bool:
    """Try to click a filter option by text. Returns True if clicked."""
    # Scope search to elements that are inside filter-related containers.
    # "span" / "label" without scope are intentionally excluded — too broad on catalog pages
    # where product card names would also match.
    scoped_selectors = [
        "[class*='filter'] button",
        "[class*='filter'] label",
        "[class*='filter'] li",
        ".filter__btn",
        ".filter-feature__btn",
        ".j-filter-item",
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

    logger.warning("[_apply_filter] Filter not found in page: %r", filter_text)
    await _dump_html(page, "filter")
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


_VARIANT_SELECTORS = [
    # WB CSS-modules pattern (class contains substring, not exact match)
    'li[class*="filterItem"]',
    'li[class*="size"]',
    ".sizes-table__item",
    ".j-size",
    ".sizes__item",
    ".product-detail__select-item",
]

_PRODUCT_PRICE_SELECTORS = [
    ".price-block__final-price",
    ".price__lower-price",
    ".product-page__price",
]


def _candidate_texts(filter_text: str) -> list[str]:
    """Return filter_text plus any numbers extracted from it.
    E.g. 'от 1900 до 2000 грамм' → ['от 1900 до 2000 грамм', '1900', '2000']
    Helps match catalog range-filters to individual variant buttons on product page.
    """
    candidates = [filter_text]
    numbers = re.findall(r"\d+", filter_text)
    candidates.extend(numbers)
    return candidates


async def _page_image_url(page) -> str | None:
    """Return the first WB CDN image URL found on the current page."""
    try:
        imgs = await page.query_selector_all("img")
        for img in imgs:
            for attr in ("src", "data-src"):
                src = await img.get_attribute(attr)
                if src and "wbbasket.ru" in src:
                    logger.debug("[_page_image_url] Found: %s", src)
                    return src
    except Exception as exc:
        logger.debug("[_page_image_url] Failed: %s", exc)
    return None


async def _resolve_variant(
    page, product_url: str, filter_items: list[str]
) -> tuple[str, float | None, str | None]:
    """Navigate to the product page, click the matching variant, extract price and image.
    Returns (url, price, image_url). Any value can be None on failure.
    """
    try:
        await page.goto(product_url, timeout=20_000)
        await page.wait_for_load_state("networkidle", timeout=15_000)

        await page.evaluate("window.scrollBy(0, 600)")
        await asyncio.sleep(1.5)

        try:
            await page.wait_for_function(
                "document.querySelectorAll('.mo-skeleton').length === 0",
                timeout=8_000,
            )
        except Exception:
            logger.debug("[_resolve_variant] Skeletons still present, continuing anyway")

        await asyncio.sleep(0.5)
    except Exception as exc:
        logger.warning("[_resolve_variant] Failed to load %s: %s", product_url, exc)
        return product_url, None, None

    await _dump_html(page, "variant")

    for filter_text in filter_items:
        for candidate in _candidate_texts(filter_text):
            for selector in _VARIANT_SELECTORS:
                try:
                    item = page.locator(selector).filter(has_text=candidate)
                    if await item.count() == 0:
                        continue
                    btn = item.first.locator("button")
                    target = btn if await btn.count() > 0 else item.first
                    await target.click(timeout=3_000)
                    await asyncio.sleep(1.5)
                    logger.debug(
                        "[_resolve_variant] Clicked variant %r (from filter %r) via %r",
                        candidate, filter_text, selector,
                    )
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
                    logger.debug(
                        "[_resolve_variant] selector=%r candidate=%r: %s", selector, candidate, exc
                    )

    logger.warning("[_resolve_variant] No matching variant for filters: %r", filter_items)
    image_url = await _page_image_url(page)
    return product_url, None, image_url


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
        # If one of the filter_items is a full WB URL, navigate to it directly —
        # URL-based filtering is far more reliable than clicking sidebar UI elements.
        direct_url = next(
            (f.strip() for f in filter_items if f.strip().startswith("https://www.wildberries.ru")),
            None,
        )
        search_url = direct_url if direct_url else build_search_url(brand)
        filters_to_apply = [] if direct_url else filter_items

        logger.debug(
            "[WbScraper.scrape_brand_with_filters] brand=%r direct_url=%r filters=%r url=%s",
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
                        await page.wait_for_selector(".product-card-list .product-card", timeout=15_000)
                    except PlaywrightTimeoutError:
                        logger.warning(
                            "[WbScraper.scrape_brand_with_filters] No products for brand=%r", brand
                        )
                        return None

                    any_filter_applied = False
                    for filter_text in filters_to_apply:
                        applied = await _apply_filter(page, filter_text)
                        if applied:
                            any_filter_applied = True
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
                        "[WbScraper.scrape_brand_with_filters] Cheapest: name=%r price=%s url=%s",
                        cheapest.name, cheapest.price, cheapest.product_url,
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


