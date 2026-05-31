import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone

import httpx

from bot.scrapers.base import BaseScraper, ScrapedProduct
from bot.scrapers._ozon_utils import (
    cheapest,
    extract_weight_grams,
    parse_price,
    weight_from_url,
)

logger = logging.getLogger(__name__)

_API_BASE = "https://www.ozon.ru/api/composer-api.bx/page/json/v2"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.ozon.ru/",
}

# Widget keys that contain product tile arrays
_TILE_WIDGETS = {"tileGridDesktop", "searchResultsV2", "catalogResultsV2"}


def _build_search_url(query: str) -> str:
    return f"/search/?text={urllib.parse.quote_plus(query)}&from_global=true"


class OzonApiScraper(BaseScraper):
    platform = "ozon"

    async def scrape(self, query: str) -> ScrapedProduct | None:
        page_url = _build_search_url(query)
        logger.debug("[OzonApiScraper.scrape] query=%r page_url=%s", query, page_url)
        return await self.scrape_brand_with_filters(query, [])

    async def scrape_brand_with_filters(
        self, brand: str, filter_items: list[str]
    ) -> ScrapedProduct | None:
        logger.debug(
            "[OzonApiScraper.scrape_brand_with_filters] brand=%r filters=%r",
            brand, filter_items,
        )

        direct_url = next(
            (f.strip() for f in filter_items if "ozon.ru" in f.strip()),
            None,
        )

        if direct_url:
            parsed = urllib.parse.urlparse(direct_url)
            page_url = parsed.path
            if parsed.query:
                page_url = f"{page_url}?{parsed.query}"
        else:
            page_url = _build_search_url(brand)

        weight_hint: int | None = None
        if not weight_from_url(direct_url or ""):
            for f in filter_items:
                if "ozon.ru" not in f:
                    w = extract_weight_grams(f)
                    if w:
                        weight_hint = w
                        logger.debug(
                            "[OzonApiScraper.scrape_brand_with_filters] weight hint %dg from %r",
                            w, f,
                        )
                        break

        logger.info(
            "[OzonApiScraper.scrape_brand_with_filters] start brand=%r url=%s",
            brand, page_url,
        )

        data = await self._fetch_page(page_url)
        if data is None:
            logger.warning(
                "[OzonApiScraper.scrape_brand_with_filters] fetch returned None for url=%s", page_url
            )
            return None

        products = self._parse_products(data, fallback_url=direct_url or page_url)
        if not products:
            logger.warning(
                "[OzonApiScraper.scrape_brand_with_filters] no products parsed from url=%s", page_url
            )
            return None

        weight_g = weight_from_url(direct_url or "") or weight_hint
        result = cheapest(products, brand=brand, weight_g=weight_g)
        if result:
            result.query = brand
            logger.info(
                "[OzonApiScraper.scrape_brand_with_filters] result name=%r price=%s url=%s",
                result.name, result.price, result.product_url,
            )
        return result

    async def _fetch_page(self, page_url: str) -> dict | None:
        api_url = f"{_API_BASE}?url={urllib.parse.quote(page_url, safe='/?=&')}"
        logger.debug("[OzonApiScraper._fetch_page] GET %s", api_url)

        proxy = os.getenv("OZON_PROXY", "").strip() or None
        proxies = {"all://": proxy} if proxy else None

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(
                    headers=_HEADERS,
                    timeout=15.0,
                    follow_redirects=True,
                    proxies=proxies,
                ) as client:
                    resp = await client.get(api_url)

                logger.debug(
                    "[OzonApiScraper._fetch_page] status=%d size=%d attempt=%d",
                    resp.status_code, len(resp.content), attempt + 1,
                )

                if resp.status_code == 200:
                    return resp.json()

                logger.warning(
                    "[OzonApiScraper._fetch_page] HTTP %d for url=%s attempt=%d",
                    resp.status_code, api_url, attempt + 1,
                )

            except httpx.TimeoutException as exc:
                logger.warning(
                    "[OzonApiScraper._fetch_page] timeout attempt=%d url=%s: %s",
                    attempt + 1, api_url, exc,
                )
            except Exception as exc:
                logger.warning(
                    "[OzonApiScraper._fetch_page] error attempt=%d url=%s: %s",
                    attempt + 1, api_url, exc,
                )

        logger.error("[OzonApiScraper._fetch_page] all attempts failed for url=%s", api_url)
        return None

    def _parse_products(self, data: dict, fallback_url: str = "") -> list[ScrapedProduct]:
        widget_states = data.get("widgetStates") or {}
        if not isinstance(widget_states, dict):
            logger.debug("[OzonApiScraper._parse_products] widgetStates is not a dict")
            return []

        results: list[ScrapedProduct] = []

        for key, raw_value in widget_states.items():
            widget_name = key.split("-")[0] if "-" in key else key
            if widget_name not in _TILE_WIDGETS:
                continue

            try:
                widget_data = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
            except (json.JSONDecodeError, TypeError) as exc:
                logger.debug(
                    "[OzonApiScraper._parse_products] JSON parse failed for widget %r: %s", key, exc
                )
                continue

            items = widget_data.get("items") or widget_data.get("products") or []
            if not isinstance(items, list):
                continue

            logger.debug(
                "[OzonApiScraper._parse_products] widget=%r items=%d", widget_name, len(items)
            )

            for item in items:
                if not isinstance(item, dict):
                    continue

                product = self._parse_tile(item, fallback_url)
                if product is not None:
                    results.append(product)

        logger.info("[OzonApiScraper._parse_products] parsed %d products", len(results))
        return results

    def _parse_tile(self, tile: dict, fallback_url: str) -> ScrapedProduct | None:
        name = (
            tile.get("name")
            or tile.get("title")
            or (tile.get("mainState") or [{}])[0].get("atom", {}).get("label", {}).get("textStyle", "")
            or "Товар"
        )
        if not isinstance(name, str):
            name = "Товар"
        name = name.strip() or "Товар"

        price_raw = self._extract_price(tile)
        if price_raw is None:
            logger.debug("[OzonApiScraper._parse_tile] no price for tile name=%r", name)
            return None

        try:
            price = parse_price(str(price_raw))
        except (ValueError, AttributeError) as exc:
            logger.debug(
                "[OzonApiScraper._parse_tile] price parse failed raw=%r: %s", price_raw, exc
            )
            return None

        url = tile.get("action", {}).get("link") or tile.get("link") or ""
        if url and not url.startswith("http"):
            url = f"https://www.ozon.ru{url}"
        if not url:
            url = fallback_url

        image_url = None
        images = tile.get("tileImage") or {}
        if isinstance(images, dict):
            image_url = images.get("src") or images.get("image")
        if not image_url:
            for img_key in ("imageURLS", "images"):
                imgs = tile.get(img_key)
                if isinstance(imgs, list) and imgs:
                    image_url = imgs[0] if isinstance(imgs[0], str) else imgs[0].get("src")
                    break

        logger.debug(
            "[OzonApiScraper._parse_tile] name=%r price=%s url=%s", name, price, url
        )

        return ScrapedProduct(
            name=name,
            price=price,
            currency="RUB",
            product_url=url,
            platform="ozon",
            query="",
            scraped_at=datetime.now(tz=timezone.utc),
            image_url=image_url,
        )

    def _extract_price(self, tile: dict) -> str | int | float | None:
        # Direct numeric fields
        for key in ("finalPrice", "price", "cardPrice"):
            val = tile.get(key)
            if val is not None:
                if isinstance(val, (int, float)):
                    return val
                if isinstance(val, str) and val.strip():
                    return val
                if isinstance(val, dict):
                    # Ozon sometimes sends {"price": {"text": "1 234 ₽"}}
                    text = val.get("text") or val.get("value") or val.get("price")
                    if text is not None:
                        return text

        # Nested price block: tile["price"]["price"], tile["price"]["originalPrice"]
        price_block = tile.get("priceForCard") or tile.get("pricePerItem")
        if isinstance(price_block, dict):
            for key in ("price", "cardPrice", "originalPrice"):
                val = price_block.get(key)
                if val is not None:
                    return val

        # mainState atom list — each atom may contain price info
        for state in tile.get("mainState") or []:
            if not isinstance(state, dict):
                continue
            atom = state.get("atom") or {}
            price_text = atom.get("price") or atom.get("priceText") or atom.get("label", {}).get("text")
            if price_text and isinstance(price_text, str) and "₽" in price_text:
                return price_text

        return None
