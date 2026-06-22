import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)
_scheduler = AsyncIOScheduler()


async def _nightly():
    from scrapers import run_all
    await run_all()


async def _weekly_grow():
    from scrapers.grow_sources import run_grow
    await run_grow(crawl_depth=2)


def setup_scheduler():
    _scheduler.add_job(
        _nightly,
        CronTrigger(hour=2, minute=0),
        id="nightly_scrape",
        replace_existing=True,
    )
    # Grow sources list every Sunday at 03:00
    _scheduler.add_job(
        _weekly_grow,
        CronTrigger(day_of_week="sun", hour=3, minute=0),
        id="weekly_grow_sources",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started — scrape nightly 02:00, grow sources weekly Sun 03:00")


def shutdown_scheduler():
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
