"""
Afternoon Umbrella Friends — San Diego social activity group.
They post events primarily on their website and Meetup group.
Update BASE_URL below once the canonical URL is confirmed.
"""
from __future__ import annotations

from .base import BaseScraper

# The group appears to list events at this URL — update if it changes.
BASE_URL = "https://www.afternoonumbrellafriends.com/"
MEETUP_URL = "https://www.meetup.com/afternoon-umbrella-friends/"


class UmbrellaFriendsScraper(BaseScraper):
    name = "umbrella-friends"

    async def scrape(self) -> list[dict]:
        events = await self._scrape_site()
        if not events:
            events = await self._scrape_meetup_group()
        return events

    async def _scrape_site(self) -> list[dict]:
        soup = await self.fetch(BASE_URL)
        if not soup:
            return []

        events = []
        # Generic heuristic: look for event/calendar sections
        for card in soup.select(
            "article, .event, .tribe-event, .event-card, [class*='event'], li.vevent"
        ):
            title_el = card.select_one("h1, h2, h3, h4, .event-title, .tribe-event-title")
            if not title_el:
                continue
            title = self.clean(title_el.get_text())
            if not title:
                continue

            date_el = card.select_one("time, .tribe-event-date-start, .event-date, abbr.dtstart")
            date_str, time_str = "", None
            if date_el:
                dt = date_el.get("datetime") or date_el.get("title") or date_el.get_text()
                date_str, time_str = self.parse_iso(dt)
            if not date_str:
                continue

            link = card.select_one("a[href]")
            url = link.get("href") if link else None
            if url and not url.startswith("http"):
                url = BASE_URL.rstrip("/") + "/" + url.lstrip("/")

            events.append(self.make_event(
                title=title,
                date=date_str,
                start_time=time_str,
                url=url,
            ))

        self.logger.info(f"Site: {len(events)} events")
        return events

    async def _scrape_meetup_group(self) -> list[dict]:
        """Scrape their Meetup group's upcoming events page."""
        soup = await self.fetch(MEETUP_URL + "events/")
        if not soup:
            return []

        events = []
        for card in soup.select("[data-element-name='event-card'], li.eventCard"):
            title_el = card.select_one("h2, h3, span.eventCardHead--name")
            if not title_el:
                continue
            title = self.clean(title_el.get_text())

            time_el = card.select_one("time")
            date_str, time_str = "", None
            if time_el:
                date_str, time_str = self.parse_iso(time_el.get("datetime", ""))
            if not date_str:
                continue

            link = card.select_one("a[href]")
            url = link.get("href") if link else None
            if url and not url.startswith("http"):
                url = "https://www.meetup.com" + url

            events.append(self.make_event(
                title=title or "",
                date=date_str,
                start_time=time_str,
                url=url,
            ))

        self.logger.info(f"Meetup group: {len(events)} events")
        return events
