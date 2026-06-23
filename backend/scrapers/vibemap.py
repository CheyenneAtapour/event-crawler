"""
Vibemap scraper — used by La Jolla by the Sea and potentially other SD
neighbourhood sites. Vibemap exposes a WP REST endpoint that returns a full
event feed with title, date, venue, description, and URL.
"""
from __future__ import annotations

import asyncio
from .base import BaseScraper

# Sites confirmed to run Vibemap with San Diego event data
VIBEMAP_SITES = [
    ("La Jolla by the Sea", "https://lajollabythesea.com"),
]


class VibeMapScraper(BaseScraper):
    name = "vibemap"

    async def scrape(self) -> list[dict]:
        all_events: list[dict] = []
        tasks = [self._scrape_site(name, base) for name, base in VIBEMAP_SITES]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (name, _), result in zip(VIBEMAP_SITES, results):
            if isinstance(result, Exception):
                self.logger.error(f"{name}: {result}")
            else:
                all_events.extend(result)
        self.logger.info(f"Vibemap total: {len(all_events)} events")
        return all_events

    async def _scrape_site(self, site_name: str, base_url: str) -> list[dict]:
        data = await self.fetch_json(
            f"{base_url}/wp-json/vibemap/v1/events-data",
            params={"per_page": 500},
        )
        if not data or not isinstance(data, dict):
            return []

        events = []
        for item in data.get("events", []):
            # Vibemap stores all event fields inside item["meta"] with vibemap_event_ prefix
            meta = item.get("meta") or {}

            date_str, time_str = self.parse_iso(
                meta.get("vibemap_event_start_date") or ""
            )
            if not date_str:
                continue

            events.append(self.make_event(
                title=self.clean(
                    meta.get("vibemap_event_name") or item.get("title")
                ) or "",
                description=self.clean(
                    meta.get("vibemap_event_description") or item.get("excerpt")
                ),
                date=date_str,
                start_time=time_str,
                end_time=self.parse_iso(meta.get("vibemap_event_end_date") or "")[1],
                venue=self.clean(meta.get("vibemap_event_venue_name")),
                address=self.clean(meta.get("vibemap_event_location")),
                url=meta.get("vibemap_event_url") or item.get("permalink"),
                image_url=item.get("featured_image"),
                price=self.clean(meta.get("vibemap_event_price")) or None,
                tags=site_name.lower().replace(" ", "-"),
            ))

        self.logger.info(f"{site_name}: {len(events)} events")
        return events
