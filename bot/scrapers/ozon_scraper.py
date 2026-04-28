import asyncio
import glob
import json
import logging
import os
import random
import re
import urllib.parse
from datetime import datetime, timezone

import nodriver as uc
from nodriver import cdp

from bot.scrapers.base import BaseScraper, ScrapedProduct

logger = logging.getLogger(__name__)


def _find_chromium_path() -> str | None:
    """Return a Chrome/Chromium executable path for nodriver.

    Priority:
    1. CHROME_EXECUTABLE_PATH env var (explicit override)
    2. Playwright's Chromium installed in the current user's cache
    3. None — nodriver will search system PATH itself
    """
    env_path = os.getenv("CHROME_EXECUTABLE_PATH")
    if env_path and os.path.isfile(env_path):
        logger.debug("[OzonScraper] Using Chrome from CHROME_EXECUTABLE_PATH: %s", env_path)
        return env_path

    home = os.path.expanduser("~")
    pattern = os.path.join(home, ".cache", "ms-playwright", "chromium-*", "chrome-linux", "chrome")
    matches = glob.glob(pattern)
    if matches:
        path = sorted(matches)[-1]
        logger.debug("[OzonScraper] Using Playwright Chromium: %s", path)
        return path

    logger.debug("[OzonScraper] No explicit Chromium found; nodriver will search system PATH")
    return None

# Injected into every new document before page JS runs to hide headless signals
_STEALTH_JS = """
try { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); } catch(e) {}
if (!window.chrome || !window.chrome.runtime) {
    window.chrome = Object.assign(window.chrome || {}, {
        runtime: { onMessage: { addListener: () => {} }, id: undefined },
        app: { isInstalled: false },
        csi: () => {},
        loadTimes: () => {},
    });
}
try {
    const origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (p) =>
        p.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery(p);
} catch(e) {}
"""

# Ozon uses tileGridDesktop on category pages, searchResultsV2 on plain search
_RESULTS_SELECTORS = [
    '[data-widget="tileGridDesktop"]',
    '[data-widget="searchResultsV2"]',
    '[data-widget="catalogResultsV2"]',
]

# nodriver's evaluate() runs an EXPRESSION, not a function.
# Do NOT wrap in () => { ... } — that returns the function object itself.

_EXTRACT_CARDS_JS = """
Array.from(document.querySelectorAll('[data-index]')).map(tile => {
    let price = null;
    for (const s of tile.querySelectorAll('span')) {
        const t = s.innerText || s.textContent || '';
        if (/\\d[\\d\\s\\u00a0\\u2009]*[₽﹩]/.test(t)) { price = t.trim(); break; }
    }
    if (!price) return null;

    const a = tile.querySelector('a[href*="/product/"]');
    const href = a ? a.getAttribute('href') : null;
    const productUrl = href
        ? (href.startsWith('http') ? href : 'https://www.ozon.ru' + href)
        : null;

    let name = a ? (a.getAttribute('title') || '').trim() : '';
    if (!name) {
        // Search leaf spans across the whole tile (no nested span children).
        // Leaf spans hold atomic text — avoids picking up badge+name concatenations.
        const BADGE = /^(осталось|хит|новинка|скидка|акция|топ|распродажа|sale|new|hot)/i;
        let best = '';
        for (const sp of tile.querySelectorAll('span')) {
            if (sp.querySelector('span')) continue; // skip non-leaf spans
            const t = (sp.innerText || sp.textContent || '').trim();
            if (t.length > best.length && t.length > 10 &&
                !/^[0-9]/.test(t) && !/[₽$%]/.test(t) && !BADGE.test(t)) {
                best = t;
            }
        }
        if (best) name = best;
    }

    let img = null;
    for (const i of tile.querySelectorAll('img')) {
        const src = i.getAttribute('src') || i.getAttribute('data-src') || '';
        if (src && (src.includes('ozon') || src.includes('cdn'))) { img = src; break; }
    }
    return { price, productUrl, name: name || 'Товар', img };
}).filter(item => item !== null)
"""

_WAIT_RESULTS_JS = (
    "["
    + ", ".join(f'"{s}"' for s in _RESULTS_SELECTORS)
    + "].some(sel => !!document.querySelector(sel))"
)

# JS returns a JSON string to bypass CDP object-serialisation quirks.
# Extracts weight-variant links and current price from an Ozon product page.
_EXTRACT_VARIANTS_JS = """
(function() {
    const variants = [];
    const widgetSels = [
        '[data-widget="webSku"]',
        '[data-widget="webVariant"]',
        '[data-widget="skuLine"]',
    ];
    for (const sel of widgetSels) {
        const widget = document.querySelector(sel);
        if (!widget) continue;
        widget.querySelectorAll('a[href]').forEach(a => {
            const href = a.getAttribute('href') || '';
            const text = (a.innerText || a.textContent || '').replace(/\\s+/g, ' ').trim();
            if (text.length > 0 && text.length < 40)
                variants.push({
                    href: href.startsWith('/') ? 'https://www.ozon.ru' + href : href,
                    text,
                });
        });
        if (variants.length) break;
    }
    // Less-strict regex: price may contain extra text ("с картой", etc.)
    let price = null;
    const priceWidget = document.querySelector('[data-widget="webPrice"]')
                     || document.querySelector('[data-widget="price"]');
    const priceRoot = priceWidget || document;
    for (const s of priceRoot.querySelectorAll('span')) {
        const t = (s.innerText || s.textContent || '').trim();
        if (/\\d[\\d\\s\\u00a0\\u2009]*[₽]/.test(t)) { price = t; break; }
    }
    return JSON.stringify({variants, price});
})()
"""

# True when a product page has rendered its price (distinct from search-tile check).
_PRODUCT_PAGE_READY_JS = (
    "!!document.querySelector('[data-widget=\"webPrice\"]') || "
    "!!document.querySelector('[data-widget=\"price\"]') || "
    "Array.from(document.querySelectorAll('span')).some(s => "
    "/\\d[\\d\\s\\u00a0]*\\s*\\u20bd/.test(s.innerText || s.textContent || ''))"
)

# True when at least one price span is visible inside a product tile
_PRICES_PRESENT_JS = (
    "Array.from(document.querySelectorAll('[data-index] span')).some(s => "
    "/\\d[\\d\\s\\u00a0\\u2009]*[\\u20bd﹩]/.test(s.innerText || s.textContent || ''))"
)


def _unwrap_cdp(obj):
    """Recursively convert nodriver CDP RemoteObject to plain Python value."""
    if not isinstance(obj, dict) or "type" not in obj:
        return obj
    t = obj.get("type")
    v = obj.get("value")
    if t in ("string", "number", "boolean"):
        return v
    if t == "object" and isinstance(v, list):
        if v and isinstance(v[0], list) and len(v[0]) == 2:
            # Object serialised as [[key, val], ...] pairs
            return {k: _unwrap_cdp(vv) for k, vv in v}
        # Array serialised as indexed list
        return [_unwrap_cdp(item) for item in v]
    if t in ("undefined", "null") or v is None:
        return None
    return v


def build_search_url(query: str) -> str:
    return f"https://www.ozon.ru/search/?text={urllib.parse.quote_plus(query)}&from_global=true"


def _parse_price(raw: str) -> float:
    cleaned = (
        raw.replace("₽", "")
        .replace("₽", "")
        .replace("\xa0", "")
        .replace(" ", "")
        .replace(" ", "")
        .replace(" ", "")
        .strip()
    )
    cleaned = re.sub(r"[^\d.,]", "", cleaned).replace(",", ".")
    return float(cleaned)


async def _dump_html(tab, name: str) -> None:
    try:
        os.makedirs("logs", exist_ok=True)
        html = await tab.get_content()
        path = f"logs/debug_{name}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.debug("[_dump_html] Saved %s (%d bytes)", path, len(html))
    except Exception as exc:
        logger.debug("[_dump_html] Failed: %s", exc)


async def _wait_for_results(tab, timeout: float = 15.0) -> bool:
    """Poll until any known results widget appears. Returns True if found."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            found = await tab.evaluate(_WAIT_RESULTS_JS)
            if found:
                logger.debug("[_wait_for_results] Results widget found")
                return True
        except Exception as exc:
            logger.debug("[_wait_for_results] eval error: %s", exc)
        await asyncio.sleep(0.8)
    return False


async def _wait_for_prices(tab, timeout: float = 10.0) -> bool:
    """Poll until at least one price span appears inside a product tile."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            found = await tab.evaluate(_PRICES_PRESENT_JS)
            if found:
                logger.debug("[_wait_for_prices] Price spans detected")
                return True
        except Exception as exc:
            logger.debug("[_wait_for_prices] eval error: %s", exc)
        await asyncio.sleep(0.8)
    return False


async def _parse_tiles(tab, fallback_url: str) -> list[ScrapedProduct]:
    """Run single-pass JS extraction of all product cards."""
    try:
        raw_items = await tab.evaluate(_EXTRACT_CARDS_JS)
    except Exception as exc:
        logger.debug("[_parse_tiles] JS evaluation failed: %s", exc)
        return []

    if not raw_items:
        logger.debug("[_parse_tiles] No items returned by JS")
        return []

    # nodriver wraps JS values in CDP RemoteObject format — unwrap recursively
    raw_items = _unwrap_cdp(raw_items)
    if not isinstance(raw_items, list):
        logger.debug("[_parse_tiles] Unexpected JS result type after unwrap: %s", type(raw_items))
        return []

    results: list[ScrapedProduct] = []
    for raw_item in raw_items:
        item = _unwrap_cdp(raw_item) if isinstance(raw_item, dict) else raw_item
        if not isinstance(item, dict):
            continue
        raw_price = item.get("price") or ""
        if not raw_price:
            continue
        try:
            price = _parse_price(str(raw_price))
        except (ValueError, AttributeError):
            logger.debug("[_parse_tiles] Price parse failed: %r", raw_price)
            continue

        results.append(ScrapedProduct(
            name=(item.get("name") or "Товар").strip() or "Товар",
            price=price,
            currency="RUB",
            product_url=item.get("productUrl") or fallback_url,
            platform="ozon",
            query="",
            scraped_at=datetime.now(tz=timezone.utc),
            image_url=item.get("img"),
        ))

    logger.debug("[_parse_tiles] Parsed %d valid products", len(results))
    return results


async def _wait_for_product_page(tab, timeout: float = 12.0) -> bool:
    """Poll until a product-page price widget is visible (not search-tile selector)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            found = await tab.evaluate(_PRODUCT_PAGE_READY_JS)
            if found:
                logger.debug("[_wait_for_product_page] price widget found")
                return True
        except Exception as exc:
            logger.debug("[_wait_for_product_page] eval error: %s", exc)
        await asyncio.sleep(0.8)
    return False


def _parse_variants_json(raw) -> dict:
    """Unwrap CDP value and JSON-parse the string returned by _EXTRACT_VARIANTS_JS."""
    if isinstance(raw, dict):
        raw = _unwrap_cdp(raw)
    if not isinstance(raw, str):
        logger.debug("[_parse_variants_json] unexpected type after unwrap: %s", type(raw))
        return {}
    try:
        return json.loads(raw)
    except Exception as exc:
        logger.debug("[_parse_variants_json] JSON parse failed: %s — raw=%r", exc, raw[:200])
        return {}


async def _resolve_weight_price(
    tab, product_url: str, weight_g: int
) -> float | None:
    """
    Navigate to the product page and return the price for the requested weight variant.

    Ozon typically lists each weight as a separate product linked inside the webSku
    widget.  We navigate to the candidate product, find a variant link whose text
    matches weight_g, follow that link, and read the price off the variant page.
    If no matching variant link is found, the current-page price is returned (the
    weight-filter URL may have already pointed us at the correct weight listing).
    """
    logger.debug("[_resolve_weight_price] url=%s weight=%dg", product_url, weight_g)
    await tab.get(product_url)
    await asyncio.sleep(random.uniform(2.0, 3.5))

    found = await _wait_for_product_page(tab, timeout=12.0)
    if not found:
        logger.warning("[_resolve_weight_price] product page price never appeared: %s", product_url)
        await _dump_html(tab, "ozon_product_no_price")

    raw = await tab.evaluate(_EXTRACT_VARIANTS_JS)
    data = _parse_variants_json(raw)

    variants: list = data.get("variants") or []
    current_price_raw: str | None = data.get("price")

    logger.debug(
        "[_resolve_weight_price] found %d variant links, current_price=%r",
        len(variants), current_price_raw,
    )

    for v in variants:
        if not isinstance(v, dict):
            continue
        w = _extract_weight_grams(v.get("text") or "")
        logger.debug("[_resolve_weight_price] variant text=%r → weight=%s", v.get("text"), w)
        if w == weight_g:
            variant_url = v.get("href") or ""
            if not variant_url:
                continue
            logger.debug("[_resolve_weight_price] following variant url=%s", variant_url)
            await tab.get(variant_url)
            await asyncio.sleep(random.uniform(2.0, 3.0))
            await _wait_for_product_page(tab, timeout=12.0)
            raw2 = await tab.evaluate(_EXTRACT_VARIANTS_JS)
            data2 = _parse_variants_json(raw2)
            price_raw = data2.get("price")
            logger.debug("[_resolve_weight_price] variant page price=%r", price_raw)
            if price_raw:
                try:
                    return _parse_price(str(price_raw))
                except (ValueError, AttributeError):
                    logger.debug("[_resolve_weight_price] price parse failed: %r", price_raw)
            return None

    # No matching variant link — trust the weight-filter URL to have shown the right product.
    logger.debug(
        "[_resolve_weight_price] no variant link for %dg; using current page price %r",
        weight_g, current_price_raw,
    )
    if not current_price_raw:
        await _dump_html(tab, "ozon_product_no_variant")
    if current_price_raw:
        try:
            return _parse_price(str(current_price_raw))
        except (ValueError, AttributeError):
            pass
    return None


_WEIGHT_RE = re.compile(
    r'(\d+(?:[.,]\d+)?)\s*(кг|kg|г(?!\w)|g(?!\w))', re.IGNORECASE
)


def _weight_from_url(url: str) -> int | None:
    """Parse weight in grams from Ozon facet filter URL (weight=2000.000;2000.000 → 2000)."""
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        raw = qs.get("weight", [""])[0].split(";")[0]
        return int(float(raw)) if raw else None
    except (ValueError, IndexError):
        return None


def _extract_weight_grams(text: str) -> int | None:
    """Extract the first weight value (in grams) from a text string."""
    for m in _WEIGHT_RE.finditer(text):
        val = float(m.group(1).replace(",", "."))
        unit = m.group(2).lower()
        return int(val * 1000) if unit in ("кг", "kg") else int(val)
    return None


def _matches_weight(product: ScrapedProduct, grams: int) -> bool:
    """True if the product name or URL matches the requested weight in grams."""
    # Prefer extracting weight from the product name (most reliable)
    w = _extract_weight_grams(product.name)
    if w is not None:
        return w == grams
    # Fall back to token search in the product URL slug
    kg = grams / 1000
    url_lower = (product.product_url or "").lower()
    tokens = [f"{grams}г", f"{grams}g"]
    if kg == int(kg):
        k = int(kg)
        tokens += [f"{k}кг", f"{k}kg"]
    return any(t in url_lower for t in tokens)


def _cheapest(
    products: list[ScrapedProduct], brand: str = "", weight_g: int | None = None
) -> ScrapedProduct | None:
    valid = [p for p in products if p.price is not None]

    # Prefer products matching the weight from the filter URL
    if weight_g:
        weight_matched = [p for p in valid if _matches_weight(p, weight_g)]
        if weight_matched:
            valid = weight_matched
        else:
            logger.debug("[_cheapest] No weight match for %dg, using all results", weight_g)

    # Prefer products whose name contains all words of the brand query
    if brand:
        words = brand.lower().split()
        brand_matched = [p for p in valid if all(w in p.name.lower() for w in words)]
        if brand_matched:
            valid = brand_matched
        else:
            logger.debug("[_cheapest] No exact brand match for %r, using all results", brand)

    return min(valid, key=lambda p: p.price) if valid else None


class OzonScraper(BaseScraper):
    platform = "ozon"

    async def scrape(self, query: str) -> ScrapedProduct | None:
        url = build_search_url(query)
        logger.debug("[OzonScraper.scrape] query=%r url=%s", query, url)
        return await self._run(brand=query, url=url)

    async def scrape_brand_with_filters(
        self, brand: str, filter_items: list[str]
    ) -> ScrapedProduct | None:
        direct_url = next(
            (f.strip() for f in filter_items if "ozon.ru" in f.strip()),
            None,
        )
        url = direct_url if direct_url else build_search_url(brand)

        # If the URL has no weight facet, try to extract weight from text filter items
        # so the user can pass e.g. "https://...ozon.ru/..., 2кг" and get the right price.
        weight_hint: int | None = None
        if not _weight_from_url(url):
            for f in filter_items:
                if "ozon.ru" not in f:
                    w = _extract_weight_grams(f)
                    if w:
                        weight_hint = w
                        logger.debug(
                            "[OzonScraper.scrape_brand_with_filters] weight hint %dg from filter %r",
                            w, f,
                        )
                        break

        logger.debug(
            "[OzonScraper.scrape_brand_with_filters] brand=%r direct_url=%s weight_hint=%s url=%s",
            brand, bool(direct_url), weight_hint, url,
        )
        return await self._run(brand=brand, url=url, weight_hint=weight_hint)

    async def _run(self, brand: str, url: str, weight_hint: int | None = None) -> ScrapedProduct | None:
        browser = None
        try:
            chromium_path = _find_chromium_path()
            start_kwargs: dict = dict(
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
            if chromium_path:
                start_kwargs["browser_executable_path"] = chromium_path
            browser = await uc.start(**start_kwargs)
            logger.debug("[OzonScraper._run] Browser started, navigating to %s", url)

            # Open blank page first so we can inject the stealth script before Ozon loads
            tab = await browser.get("about:blank")
            await tab.send(cdp.page.add_script_to_evaluate_on_new_document(_STEALTH_JS))
            await tab.get(url)
            await asyncio.sleep(random.uniform(3.0, 5.0))

            found = await _wait_for_results(tab, timeout=15.0)
            if not found:
                logger.warning("[OzonScraper._run] No results widget found: url=%s", url)
                await _dump_html(tab, "ozon_search")
                return None

            # Multi-step scroll to trigger lazy-loaded prices in headless mode.
            # Ozon renders the grid container early but populates price spans only
            # after tiles enter the viewport via IntersectionObserver.
            for scroll_y in (600, 1200, 1800):
                await tab.evaluate(f"window.scrollTo(0, {scroll_y})")
                await asyncio.sleep(1.2)

            # Scroll back to top so the first tiles are in view again
            await tab.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.8)

            # Wait until at least one price appears (up to 10 s)
            prices_ready = await _wait_for_prices(tab, timeout=10.0)
            if not prices_ready:
                logger.warning(
                    "[OzonScraper._run] Price spans never appeared in headless mode: url=%s", url
                )
                await _dump_html(tab, "ozon_no_prices")
                return None

            products = await _parse_tiles(tab, url)
            if not products:
                logger.warning("[OzonScraper._run] No parseable tiles: url=%s", url)
                await _dump_html(tab, "ozon_empty")
                return None

            weight_g = _weight_from_url(url) or weight_hint
            # When a weight filter is present, Ozon cards rarely embed the weight in
            # the product name, so name-based weight matching fails and _cheapest falls
            # back to the cheapest overall (= lightest variant).  We still run _cheapest
            # for brand filtering, then verify / correct the price on the product page.
            cheapest = _cheapest(products, brand=brand, weight_g=weight_g)
            if cheapest:
                cheapest.query = brand
                if weight_g:
                    corrected = await _resolve_weight_price(
                        tab, cheapest.product_url, weight_g
                    )
                    if corrected is not None:
                        logger.info(
                            "[OzonScraper._run] Price corrected %s→%s for weight=%dg",
                            cheapest.price, corrected, weight_g,
                        )
                        cheapest.price = corrected
                logger.info(
                    "[OzonScraper._run] Result: name=%r price=%s url=%s",
                    cheapest.name, cheapest.price, cheapest.product_url,
                )
            return cheapest

        except Exception as exc:
            logger.error("[OzonScraper._run] Error: url=%s — %s", url, exc, exc_info=True)
            return None
        finally:
            if browser:
                try:
                    browser.stop()
                except Exception:
                    pass


