import logging
import os

from bot.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


def make_ozon_scraper(settings=None) -> BaseScraper:
    """Return OzonApiScraper or OzonScraper based on OZON_SCRAPER_BACKEND."""
    backend = (
        settings.OZON_SCRAPER_BACKEND
        if settings is not None
        else os.getenv("OZON_SCRAPER_BACKEND", "browser")
    )
    logger.info("[factory] ozon backend=%s", backend)

    if backend == "api":
        from bot.scrapers.ozon_api_scraper import OzonApiScraper
        return OzonApiScraper()

    from bot.scrapers.ozon_scraper import OzonScraper
    return OzonScraper()
