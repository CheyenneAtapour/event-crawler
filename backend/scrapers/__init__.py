import asyncio
import logging
from .nineteen_hz import NineteenHzScraper
from .meetup import MeetupScraper
from .events_com import EventsComScraper
from .umbrella import UmbrellaFriendsScraper
from .venues import VenuesScraper
from .social import FacebookScraper, InstagramScraper
from .dynamic_venues import DynamicVenuesScraper

logger = logging.getLogger(__name__)

SCRAPERS = [
    NineteenHzScraper,
    MeetupScraper,
    EventsComScraper,
    UmbrellaFriendsScraper,
    VenuesScraper,
    FacebookScraper,
    InstagramScraper,
    DynamicVenuesScraper,   # reads sources.txt + finds social links per venue
]


async def run_all() -> int:
    from db import upsert_events

    total = 0
    for Cls in SCRAPERS:
        scraper = Cls()
        try:
            logger.info(f"▶ {scraper.name}")
            events = await scraper.scrape()
            n = upsert_events(events)
            total += n
            logger.info(f"  {scraper.name}: scraped={len(events)} saved={n}")
        except Exception as exc:
            logger.error(f"  {scraper.name} FAILED: {exc}", exc_info=True)

    logger.info(f"Done — {total} events saved total")
    return total
