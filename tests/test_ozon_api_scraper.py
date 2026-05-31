"""Unit tests for OzonApiScraper._parse_products."""
import json
import logging
from datetime import datetime, timezone

import pytest

from bot.scrapers.ozon_api_scraper import OzonApiScraper
from bot.scrapers.base import ScrapedProduct

logger = logging.getLogger(__name__)


def _scraper() -> OzonApiScraper:
    return OzonApiScraper()


def _make_widget_states(widget_name: str, items: list) -> dict:
    """Helper: build a widgetStates dict with one tile widget."""
    return {
        f"{widget_name}-123456": json.dumps({"items": items})
    }


# ---------------------------------------------------------------------------
# _parse_products: happy path
# ---------------------------------------------------------------------------

def test_parse_products_returns_product_with_correct_price() -> None:
    scraper = _scraper()
    tile = {
        "name": "Корм для кошек Royal Canin 2кг",
        "finalPrice": 1234,
        "action": {"link": "/product/royal-canin-2kg-123/"},
    }
    data = {"widgetStates": _make_widget_states("tileGridDesktop", [tile])}

    products = scraper._parse_products(data, fallback_url="https://www.ozon.ru/search/")

    logger.debug("[test] products=%r", products)
    assert len(products) == 1
    p = products[0]
    assert p.name == "Корм для кошек Royal Canin 2кг"
    assert p.price == 1234.0
    assert p.currency == "RUB"
    assert p.platform == "ozon"
    assert "ozon.ru" in p.product_url


def test_parse_products_price_as_string_with_ruble_sign() -> None:
    scraper = _scraper()
    tile = {
        "name": "Товар",
        "finalPrice": "2 599 ₽",
        "action": {"link": "/product/test-456/"},
    }
    data = {"widgetStates": _make_widget_states("searchResultsV2", [tile])}

    products = scraper._parse_products(data)

    assert len(products) == 1
    assert products[0].price == pytest.approx(2599.0)


def test_parse_products_price_in_dict_text_field() -> None:
    scraper = _scraper()
    tile = {
        "name": "Товар dict price",
        "price": {"text": "3 000 ₽"},
        "action": {"link": "/product/dict-789/"},
    }
    data = {"widgetStates": _make_widget_states("catalogResultsV2", [tile])}

    products = scraper._parse_products(data)

    assert len(products) == 1
    assert products[0].price == pytest.approx(3000.0)


# ---------------------------------------------------------------------------
# _parse_products: empty / no-match cases
# ---------------------------------------------------------------------------

def test_parse_products_empty_response_returns_empty_list() -> None:
    scraper = _scraper()
    data = {"widgetStates": {}}

    products = scraper._parse_products(data)

    assert products == []


def test_parse_products_wrong_widget_type_is_skipped() -> None:
    scraper = _scraper()
    tile = {"name": "Реклама", "finalPrice": 999}
    # Widget key that is NOT in _TILE_WIDGETS
    widget_states = {"banner-111": json.dumps({"items": [tile]})}
    data = {"widgetStates": widget_states}

    products = scraper._parse_products(data)

    assert products == []


def test_parse_products_invalid_price_is_skipped() -> None:
    scraper = _scraper()
    tile = {
        "name": "Бесценный товар",
        "finalPrice": "бесплатно",
        "action": {"link": "/product/free-item/"},
    }
    data = {"widgetStates": _make_widget_states("tileGridDesktop", [tile])}

    products = scraper._parse_products(data)

    assert products == []


def test_parse_products_missing_price_is_skipped() -> None:
    scraper = _scraper()
    tile = {
        "name": "Товар без цены",
        "action": {"link": "/product/no-price/"},
    }
    data = {"widgetStates": _make_widget_states("tileGridDesktop", [tile])}

    products = scraper._parse_products(data)

    assert products == []


# ---------------------------------------------------------------------------
# _parse_products: multiple items, fallback URL
# ---------------------------------------------------------------------------

def test_parse_products_multiple_items() -> None:
    scraper = _scraper()
    tiles = [
        {"name": f"Товар {i}", "finalPrice": 100 * i, "action": {"link": f"/product/item-{i}/"}}
        for i in range(1, 4)
    ]
    data = {"widgetStates": _make_widget_states("tileGridDesktop", tiles)}

    products = scraper._parse_products(data)

    assert len(products) == 3
    prices = {p.price for p in products}
    assert prices == {100.0, 200.0, 300.0}


def test_parse_products_uses_fallback_url_when_no_link() -> None:
    scraper = _scraper()
    tile = {"name": "Товар без ссылки", "finalPrice": 500}
    data = {"widgetStates": _make_widget_states("tileGridDesktop", [tile])}
    fallback = "https://www.ozon.ru/search/?text=test"

    products = scraper._parse_products(data, fallback_url=fallback)

    assert len(products) == 1
    assert products[0].product_url == fallback


def test_parse_products_relative_url_gets_ozon_prefix() -> None:
    scraper = _scraper()
    tile = {"name": "Товар", "finalPrice": 777, "action": {"link": "/product/rel-link-789/"}}
    data = {"widgetStates": _make_widget_states("tileGridDesktop", [tile])}

    products = scraper._parse_products(data)

    assert products[0].product_url == "https://www.ozon.ru/product/rel-link-789/"


# ---------------------------------------------------------------------------
# _parse_products: widgetStates value is already a dict (not JSON string)
# ---------------------------------------------------------------------------

def test_parse_products_widget_state_as_dict() -> None:
    scraper = _scraper()
    tile = {"name": "Товар dict", "finalPrice": 888, "action": {"link": "/product/dict-state/"}}
    data = {
        "widgetStates": {
            "tileGridDesktop-999": {"items": [tile]}  # dict, not JSON string
        }
    }

    products = scraper._parse_products(data)

    assert len(products) == 1
    assert products[0].price == pytest.approx(888.0)
