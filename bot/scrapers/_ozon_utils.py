"""Shared utilities used by both OzonScraper (browser) and OzonApiScraper (HTTP API)."""
import re
import urllib.parse
import logging

from bot.scrapers.base import ScrapedProduct

logger = logging.getLogger(__name__)

_WEIGHT_RE = re.compile(
    r'(\d+(?:[.,]\d+)?)\s*(кг|kg|г(?!\w)|g(?!\w))', re.IGNORECASE
)


def parse_price(raw: str) -> float:
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


def weight_from_url(url: str) -> int | None:
    """Parse weight in grams from Ozon facet filter URL (weight=2000.000;2000.000 → 2000)."""
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        raw = qs.get("weight", [""])[0].split(";")[0]
        return int(float(raw)) if raw else None
    except (ValueError, IndexError):
        return None


def extract_weight_grams(text: str) -> int | None:
    """Extract the first weight value (in grams) from a text string."""
    for m in _WEIGHT_RE.finditer(text):
        val = float(m.group(1).replace(",", "."))
        unit = m.group(2).lower()
        return int(val * 1000) if unit in ("кг", "kg") else int(val)
    return None


def matches_weight(product: ScrapedProduct, grams: int) -> bool:
    """True if the product name or URL matches the requested weight in grams."""
    w = extract_weight_grams(product.name)
    if w is not None:
        return w == grams
    kg = grams / 1000
    url_lower = (product.product_url or "").lower()
    tokens = [f"{grams}г", f"{grams}g"]
    if kg == int(kg):
        k = int(kg)
        tokens += [f"{k}кг", f"{k}kg"]
    return any(t in url_lower for t in tokens)


def cheapest(
    products: list[ScrapedProduct], brand: str = "", weight_g: int | None = None
) -> ScrapedProduct | None:
    valid = [p for p in products if p.price is not None]

    if weight_g:
        weight_matched = [p for p in valid if matches_weight(p, weight_g)]
        if weight_matched:
            valid = weight_matched
        else:
            logger.debug("[cheapest] No weight match for %dg, using all results", weight_g)

    if brand:
        words = brand.lower().split()
        brand_matched = [p for p in valid if all(w in p.name.lower() for w in words)]
        if brand_matched:
            valid = brand_matched
        else:
            logger.debug("[cheapest] No exact brand match for %r, using all results", brand)

    return min(valid, key=lambda p: p.price) if valid else None
