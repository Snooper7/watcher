import logging
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.database.models import Base, Platform, PriceRecord, Product, User

logger = logging.getLogger(__name__)

TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture
def engine():
    logger.debug("[test_database] Creating in-memory SQLite engine")
    eng = create_engine(TEST_DB_URL)
    Base.metadata.create_all(eng)
    logger.debug("[test_database] Tables created: %s", Base.metadata.tables.keys())
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    Session = sessionmaker(bind=engine)
    sess = Session()
    logger.debug("[test_database] Session opened")
    yield sess
    sess.close()
    logger.debug("[test_database] Session closed")


def test_init_db_creates_tables(engine):
    from sqlalchemy import inspect
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    logger.debug("[test_init_db_creates_tables] Tables: %s", tables)
    assert "users" in tables
    assert "products" in tables
    assert "price_records" in tables


def test_create_user(session):
    user = User(telegram_id=123456789, username="testuser")
    session.add(user)
    session.commit()
    logger.debug("[test_create_user] Created user: %s", user)

    found = session.query(User).filter_by(telegram_id=123456789).first()
    assert found is not None
    assert found.username == "testuser"
    assert found.telegram_id == 123456789


def test_create_product(session):
    user = User(telegram_id=111222333, username="buyer")
    session.add(user)
    session.flush()
    logger.debug("[test_create_product] User created with id=%s", user.id)

    product = Product(
        user_id=user.id,
        name="iPhone 15",
        wb_url="https://www.wildberries.ru/catalog/123",
        ozon_url="https://www.ozon.ru/product/456",
    )
    session.add(product)
    session.commit()
    logger.debug("[test_create_product] Created product: %s", product)

    found = session.query(Product).filter_by(user_id=user.id).first()
    assert found is not None
    assert found.name == "iPhone 15"
    assert found.wb_url is not None
    assert found.ozon_url is not None


def test_create_price_record(session):
    user = User(telegram_id=999888777, username="watcher")
    session.add(user)
    session.flush()

    product = Product(user_id=user.id, name="Samsung TV")
    session.add(product)
    session.flush()
    logger.debug("[test_create_price_record] Product created with id=%s", product.id)

    record = PriceRecord(
        product_id=product.id,
        platform=Platform.wb,
        price=49999.0,
        currency="RUB",
    )
    session.add(record)
    session.commit()
    logger.debug("[test_create_price_record] Created price record: %s", record)

    found = session.query(PriceRecord).filter_by(product_id=product.id).first()
    assert found is not None
    assert found.platform == Platform.wb
    assert found.price == 49999.0
    assert found.currency == "RUB"
