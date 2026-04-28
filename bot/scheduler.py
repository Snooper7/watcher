import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application

from bot.config import Settings
from bot.database.db import list_all_products_with_urls, save_price_record
from bot.scrapers.ozon_scraper import OzonScraper
from bot.scrapers.wb_scraper import WbScraper

logger = logging.getLogger(__name__)

_ozon_scraper = OzonScraper()
_wb_scraper = WbScraper()


async def collect_prices(app: Application) -> None:
    logger.info("[collect_prices] Starting scheduled price collection")
    started_at = time.monotonic()

    products = list_all_products_with_urls()
    logger.debug("[collect_prices] Products to check: %d", len(products))

    checked = 0
    saved = 0
    errors = 0

    for product in products:
        product_start = time.monotonic()
        try:
            if not product.ozon_url and not product.wb_url:
                logger.debug(
                    "[collect_prices] Skipping product_id=%d — no URL", product.id
                )
                continue

            if product.ozon_url:
                platform = "ozon"
                scraper = _ozon_scraper
                url = product.ozon_url
            else:
                platform = "wb"
                scraper = _wb_scraper
                url = product.wb_url

            logger.debug(
                "[collect_prices] Checking product_id=%d name=%r platform=%s",
                product.id, product.name, platform,
            )

            result = await scraper.scrape_brand_with_filters(product.name, [url])
            checked += 1

            if result is not None:
                save_price_record(product.id, result)
                saved += 1
                elapsed = time.monotonic() - product_start
                logger.debug(
                    "[collect_prices] product_id=%d price=%s platform=%s elapsed=%.2fs",
                    product.id, result.price, platform, elapsed,
                )
            else:
                logger.warning(
                    "[collect_prices] No result for product_id=%d name=%r",
                    product.id, product.name,
                )

        except Exception as exc:
            errors += 1
            logger.error(
                "[collect_prices] Error for product_id=%d name=%r: %s",
                product.id, product.name, exc, exc_info=True,
            )

    total_elapsed = time.monotonic() - started_at
    logger.info(
        "[collect_prices] Done: checked=%d saved=%d errors=%d elapsed=%.1fs",
        checked, saved, errors, total_elapsed,
    )


def setup_scheduler(app: Application, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    for time_str in settings.CHECK_TIMES:
        time_str = time_str.strip()
        try:
            hour, minute = time_str.split(":")
            trigger = CronTrigger(hour=int(hour), minute=int(minute))
            scheduler.add_job(
                collect_prices,
                trigger,
                args=[app],
                id=f"collect_prices_{time_str}",
            )
            logger.debug("[setup_scheduler] Registered job at %s", time_str)
        except (ValueError, AttributeError) as exc:
            logger.error("[setup_scheduler] Invalid CHECK_TIMES entry %r: %s", time_str, exc)

    products = list_all_products_with_urls()
    logger.info(
        "[setup_scheduler] Configured: schedule=%s products_monitored=%d",
        settings.CHECK_TIMES, len(products),
    )
    return scheduler
