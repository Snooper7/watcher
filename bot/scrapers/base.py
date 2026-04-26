import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ScrapedProduct:
    name: str
    price: float | None
    currency: str
    product_url: str
    platform: str
    query: str
    scraped_at: datetime
    image_url: str | None = None


class BaseScraper(ABC):
    def __init__(self) -> None:
        logger.debug("[%s.__init__] Scraper instance created, platform=%s", self.__class__.__name__, self.platform)

    @property
    @abstractmethod
    def platform(self) -> str: ...

    @abstractmethod
    async def scrape(self, query: str) -> ScrapedProduct | None:
        """query — название товара, опционально с производителем: 'Nike Air Force 1 Nike'"""
        ...
