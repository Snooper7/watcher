import logging
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from bot.database.models import Base, Favorite, Platform, PriceRecord, Product, User
from bot.scrapers.base import ScrapedProduct

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionFactory: sessionmaker | None = None


def init_db(database_url: str) -> Engine:
    global _engine, _SessionFactory

    logger.debug("[init_db] Initializing database: %s", database_url)

    if _engine is not None:
        logger.debug("[init_db] Reusing existing engine")
        return _engine

    # Enable SQL echo only in DEBUG mode
    echo = os.getenv("LOG_LEVEL", "DEBUG").upper() == "DEBUG"

    _engine = create_engine(database_url, echo=echo)
    _SessionFactory = sessionmaker(bind=_engine)

    # Ensure data directory exists for SQLite
    if database_url.startswith("sqlite:///"):
        db_path = database_url.replace("sqlite:///", "")
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
            logger.debug("[init_db] Ensured data directory exists: %s", db_dir)

    Base.metadata.create_all(_engine)

    inspector = inspect(_engine)
    tables = inspector.get_table_names()
    logger.info("[init_db] Database initialized. Tables created: %s", tables)

    return _engine


def save_favorite(telegram_id: int, brand: str, filters: str | None) -> Favorite:
    with get_session() as session:
        fav = Favorite(telegram_id=telegram_id, brand=brand, filters=filters)
        session.add(fav)
        session.flush()
        session.expunge(fav)
        logger.info("[save_favorite] Saved: telegram_id=%s brand=%r", telegram_id, brand)
        return fav


def get_favorites(telegram_id: int) -> list[Favorite]:
    with get_session() as session:
        stmt = (
            select(Favorite)
            .where(Favorite.telegram_id == telegram_id)
            .order_by(Favorite.created_at.desc())
        )
        favs = list(session.execute(stmt).scalars().all())
        session.expunge_all()
        logger.debug("[get_favorites] telegram_id=%s count=%d", telegram_id, len(favs))
        return favs


def get_favorite_by_id(fav_id: int) -> Favorite | None:
    with get_session() as session:
        fav = session.get(Favorite, fav_id)
        if fav is not None:
            session.expunge(fav)
        return fav


def get_or_create_user(telegram_id: int, username: str | None) -> User:
    with get_session() as session:
        logger.debug("[get_or_create_user] telegram_id=%s username=%r", telegram_id, username)
        stmt = select(User).where(User.telegram_id == telegram_id)
        user = session.execute(stmt).scalars().first()
        if user is not None:
            logger.debug("[get_or_create_user] found existing user id=%s", user.id)
            session.expunge(user)
            return user
        user = User(telegram_id=telegram_id, username=username)
        session.add(user)
        session.flush()
        session.expunge(user)
        logger.info("[get_or_create_user] created new user id=%s telegram_id=%s", user.id, telegram_id)
        return user


def add_product(
    user_id: int,
    name: str,
    wb_url: str | None = None,
    ozon_url: str | None = None,
) -> Product:
    with get_session() as session:
        logger.debug(
            "[add_product] user_id=%s name=%r wb_url=%r ozon_url=%r",
            user_id, name, wb_url, ozon_url,
        )
        product = Product(user_id=user_id, name=name, wb_url=wb_url, ozon_url=ozon_url)
        session.add(product)
        session.flush()
        session.expunge(product)
        logger.info("[add_product] saved product id=%s name=%r user_id=%s", product.id, name, user_id)
        return product


def list_products(user_id: int) -> list[Product]:
    with get_session() as session:
        logger.debug("[list_products] user_id=%s", user_id)
        stmt = (
            select(Product)
            .where(Product.user_id == user_id)
            .order_by(Product.added_at.desc())
        )
        products = list(session.execute(stmt).scalars().all())
        session.expunge_all()
        logger.debug("[list_products] user_id=%s count=%d", user_id, len(products))
        return products


def get_product_by_id(product_id: int) -> Product | None:
    with get_session() as session:
        logger.debug("[get_product_by_id] product_id=%s", product_id)
        product = session.get(Product, product_id)
        if product is not None:
            session.expunge(product)
            logger.debug("[get_product_by_id] found product id=%s name=%r", product.id, product.name)
        else:
            logger.debug("[get_product_by_id] product_id=%s not found", product_id)
        return product


def remove_product(product_id: int) -> None:
    with get_session() as session:
        logger.debug("[remove_product] product_id=%s", product_id)
        product = session.get(Product, product_id)
        if product is None:
            logger.warning("[remove_product] product_id=%s not found, skipping", product_id)
            return
        name = product.name
        session.delete(product)
        logger.info("[remove_product] deleted product id=%s name=%r", product_id, name)


def list_all_products_with_urls() -> list[Product]:
    with get_session() as session:
        stmt = select(Product).where(
            (Product.wb_url.is_not(None)) | (Product.ozon_url.is_not(None))
        )
        products = list(session.execute(stmt).scalars().all())
        session.expunge_all()
        logger.debug("[list_all_products_with_urls] count=%d", len(products))
        return products


def save_price_record(product_id: int, scraped: ScrapedProduct) -> PriceRecord:
    platform = Platform.ozon if scraped.platform == "ozon" else Platform.wb
    logger.debug(
        "[save_price_record] product_id=%d price=%s platform=%s",
        product_id, scraped.price, platform.value,
    )
    with get_session() as session:
        record = PriceRecord(
            product_id=product_id,
            platform=platform,
            price=scraped.price,
            currency=scraped.currency,
        )
        session.add(record)
        session.flush()

        product = session.get(Product, product_id)
        if product is not None:
            if platform == Platform.ozon and not product.ozon_url:
                product.ozon_url = scraped.product_url
                logger.debug("[save_price_record] Updated ozon_url for product_id=%d", product_id)
            elif platform == Platform.wb and not product.wb_url:
                product.wb_url = scraped.product_url
                logger.debug("[save_price_record] Updated wb_url for product_id=%d", product_id)

        record_id = record.id
        session.expunge(record)
        logger.info(
            "[save_price_record] Saved PriceRecord id=%s price=%s platform=%s",
            record_id, scraped.price, platform.value,
        )
        return record


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine


@contextmanager
def get_session() -> Generator[Session, None, None]:
    if _SessionFactory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    session: Session = _SessionFactory()
    logger.debug("[get_session] Opening database session")
    try:
        yield session
        session.commit()
        logger.debug("[get_session] Session committed successfully")
    except Exception as exc:
        session.rollback()
        logger.error("[get_session] Session rollback due to error: %s", exc, exc_info=True)
        raise
    finally:
        session.close()
        logger.debug("[get_session] Session closed")
