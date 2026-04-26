import logging
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger, DateTime, Enum, Float, ForeignKey,
    Integer, String, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Platform(PyEnum):
    wb = "wb"
    ozon = "ozon"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    products: Mapped[list["Product"]] = relationship("Product", back_populates="user")

    def __repr__(self) -> str:
        return f"<User id={self.id} telegram_id={self.telegram_id} username={self.username}>"


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    wb_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    ozon_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="products")
    price_records: Mapped[list["PriceRecord"]] = relationship("PriceRecord", back_populates="product")

    def __repr__(self) -> str:
        return f"<Product id={self.id} name={self.name!r} user_id={self.user_id}>"


class PriceRecord(Base):
    __tablename__ = "price_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"), nullable=False)
    platform: Mapped[Platform] = mapped_column(Enum(Platform), nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    checked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped["Product"] = relationship("Product", back_populates="price_records")

    def __repr__(self) -> str:
        return (
            f"<PriceRecord id={self.id} product_id={self.product_id} "
            f"platform={self.platform.value} price={self.price}>"
        )


logger.debug("[models] Registered tables: %s", [User.__tablename__, Product.__tablename__, PriceRecord.__tablename__])
