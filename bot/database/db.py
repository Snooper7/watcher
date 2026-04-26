import logging
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from bot.database.models import Base

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
