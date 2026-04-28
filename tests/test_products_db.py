import logging

import pytest

from datetime import datetime, timezone

import bot.database.db as db_module
from bot.database.db import (
    add_product,
    get_or_create_user,
    get_product_by_id,
    init_db,
    list_all_products_with_urls,
    list_products,
    remove_product,
    save_price_record,
)
from bot.database.models import Base, Platform
from bot.scrapers.base import ScrapedProduct

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def fresh_db():
    db_module._engine = None
    db_module._SessionFactory = None
    engine = init_db("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    logger.debug("[fresh_db] in-memory SQLite initialized")
    yield
    Base.metadata.drop_all(engine)
    engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None
    logger.debug("[fresh_db] in-memory SQLite torn down")


def test_get_or_create_user_creates_new():
    user = get_or_create_user(111, "alice")
    logger.debug("[test] created user id=%s telegram_id=%s", user.id, user.telegram_id)
    assert user.telegram_id == 111
    assert user.username == "alice"
    assert user.id is not None


def test_get_or_create_user_idempotent():
    user1 = get_or_create_user(222, "bob")
    user2 = get_or_create_user(222, "bob")
    logger.debug("[test] user1.id=%s user2.id=%s", user1.id, user2.id)
    assert user1.id == user2.id


def test_get_or_create_user_none_username():
    user = get_or_create_user(333, None)
    assert user.username is None
    assert user.telegram_id == 333


def test_add_product_wb_url():
    user = get_or_create_user(444, None)
    p = add_product(user.id, "WB Product", wb_url="https://wildberries.ru/catalog/1")
    logger.debug("[test] product id=%s wb_url=%r ozon_url=%r", p.id, p.wb_url, p.ozon_url)
    assert p.wb_url == "https://wildberries.ru/catalog/1"
    assert p.ozon_url is None
    assert p.name == "WB Product"


def test_add_product_ozon_url():
    user = get_or_create_user(555, None)
    p = add_product(user.id, "Ozon Product", ozon_url="https://ozon.ru/product/123")
    logger.debug("[test] product id=%s wb_url=%r ozon_url=%r", p.id, p.wb_url, p.ozon_url)
    assert p.ozon_url == "https://ozon.ru/product/123"
    assert p.wb_url is None


def test_add_product_no_url():
    user = get_or_create_user(666, None)
    p = add_product(user.id, "Plain Product")
    logger.debug("[test] product id=%s no urls", p.id)
    assert p.wb_url is None
    assert p.ozon_url is None
    assert p.name == "Plain Product"


def test_list_products_returns_all():
    user = get_or_create_user(777, None)
    add_product(user.id, "Product A")
    add_product(user.id, "Product B")
    add_product(user.id, "Product C")
    products = list_products(user.id)
    logger.debug("[test] list count=%d", len(products))
    assert len(products) == 3


def test_list_products_empty():
    user = get_or_create_user(888, None)
    products = list_products(user.id)
    logger.debug("[test] list empty for user_id=%s", user.id)
    assert products == []


def test_list_products_only_own():
    user1 = get_or_create_user(901, None)
    user2 = get_or_create_user(902, None)
    add_product(user1.id, "User1 Product")
    add_product(user2.id, "User2 Product")
    products = list_products(user1.id)
    logger.debug("[test] user1 products count=%d", len(products))
    assert len(products) == 1
    assert products[0].name == "User1 Product"


def test_get_product_by_id_found():
    user = get_or_create_user(910, None)
    p = add_product(user.id, "Find Me")
    found = get_product_by_id(p.id)
    logger.debug("[test] found product id=%s name=%r", found.id if found else None, found.name if found else None)
    assert found is not None
    assert found.id == p.id
    assert found.name == "Find Me"


def test_get_product_by_id_not_found():
    found = get_product_by_id(99999)
    logger.debug("[test] product 99999 not found: %s", found)
    assert found is None


def test_remove_product():
    user = get_or_create_user(920, None)
    p = add_product(user.id, "Delete Me")
    remove_product(p.id)
    after = get_product_by_id(p.id)
    logger.debug("[test] after remove: %s", after)
    assert after is None


def test_remove_product_not_found_no_error():
    remove_product(99999)


def test_list_all_products_with_urls_returns_only_those_with_url():
    user = get_or_create_user(930, None)
    p_wb = add_product(user.id, "WB Product", wb_url="https://wildberries.ru/catalog/1")
    p_ozon = add_product(user.id, "Ozon Product", ozon_url="https://ozon.ru/product/1")
    _p_none = add_product(user.id, "Plain Product")

    results = list_all_products_with_urls()
    ids = {p.id for p in results}
    logger.debug("[test] list_all_products_with_urls ids=%s", ids)

    assert p_wb.id in ids
    assert p_ozon.id in ids
    assert _p_none.id not in ids


def test_list_all_products_with_urls_empty():
    user = get_or_create_user(931, None)
    add_product(user.id, "No URL Product")
    results = list_all_products_with_urls()
    assert results == []


def test_save_price_record_creates_record():
    user = get_or_create_user(940, None)
    product = add_product(user.id, "Test Product", ozon_url="https://ozon.ru/product/1")

    scraped = ScrapedProduct(
        name="Test Product",
        price=1299.0,
        currency="RUB",
        product_url="https://ozon.ru/product/1",
        platform="ozon",
        query="Test Product",
        scraped_at=datetime.now(tz=timezone.utc),
    )
    record = save_price_record(product.id, scraped)
    logger.debug("[test] PriceRecord id=%s price=%s platform=%s", record.id, record.price, record.platform)

    assert record.id is not None
    assert record.price == 1299.0
    assert record.currency == "RUB"
    assert record.platform == Platform.ozon


def test_save_price_record_wb():
    user = get_or_create_user(941, None)
    product = add_product(user.id, "WB Product", wb_url="https://wildberries.ru/catalog/1")

    scraped = ScrapedProduct(
        name="WB Product",
        price=799.0,
        currency="RUB",
        product_url="https://wildberries.ru/catalog/1",
        platform="wb",
        query="WB Product",
        scraped_at=datetime.now(tz=timezone.utc),
    )
    record = save_price_record(product.id, scraped)

    assert record.platform == Platform.wb
    assert record.price == 799.0
