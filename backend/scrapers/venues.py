"""
San Diego venue scrapers.
Each venue in VENUES gets its own fetch + parse strategy.
Many smaller venues use Eventbrite-hosted pages or a WordPress/Squarespace calendar plugin.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .base import BaseScraper

# ── Venue registry ───────────────────────────────────────────────────────────
# Each entry: (name, events_url, parser_method_name)
VENUES = [
    ("The Casbah",            "https://casbahmusic.com/",              "parse_casbah"),
    ("Belly Up",              "https://www.bellyup.com/calendar/",     "parse_generic_json_ld"),
    ("Observatory North Park","https://www.observatorynorthpark.com/", "parse_observatory"),
    ("Soda Bar",              "https://sodabarmusic.com/",             "parse_generic_json_ld"),
    ("The Irenic",            "https://www.theirenic.com/events/",     "parse_generic_json_ld"),
    ("Music Box SD",          "https://musicboxsd.com/events/",        "parse_generic_json_ld"),
    ("SOMA San Diego",        "https://somasandiego.com/",             "parse_generic_json_ld"),
    ("House of Blues SD",     "https://www.houseofblues.com/sandiego/","parse_hob"),
    ("The Quartyard",         "https://www.thequartyard.com/events",   "parse_generic_json_ld"),
    ("Whistle Stop Bar",      "https://www.whistlestopbar.com/events", "parse_generic_json_ld"),
    ("4th & B",               "https://www.4thandb.com/calendar/",     "parse_generic_json_ld"),
]


class VenuesScraper(BaseScraper):
    name = "venues"

    async def scrape(self) -> list[dict]:
        import asyncio
        tasks = [self._scrape_venue(*v) for v in VENUES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        events: list[dict] = []
        for venue_name, result in zip([v[0] for v in VENUES], results):
            if isinstance(result, Exception):
                self.logger.error(f"{venue_name}: {result}")
            else:
                events.extend(result)

        self.logger.info(f"Venues total: {len(events)} events")
        return events

    async def _scrape_venue(self, name: str, url: str, parser: str) -> list[dict]:
        soup = await self.fetch(url)
        if not soup:
            return []
        method = getattr(self, parser)
        events = method(soup, name, url)
        self.logger.debug(f"{name}: {len(events)} events")
        return events

    # ── Generic JSON-LD parser (works for many modern venue sites) ───────────

    def parse_generic_json_ld(self, soup, venue_name: str, base_url: str) -> list[dict]:
        import json
        events = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") not in ("Event", "MusicEvent", "SocialEvent"):
                    continue
                ev = self._from_json_ld(item, venue_name)
                if ev:
                    events.append(ev)

        # Fallback to common HTML patterns if JSON-LD found nothing
        if not events:
            events = self._parse_html_events(soup, venue_name, base_url)
        return events

    def _from_json_ld(self, item: dict, venue_name: str) -> Optional[dict]:
        name = self.clean(item.get("name"))
        if not name:
            return None

        start = item.get("startDate") or ""
        date_str, time_str = self.parse_iso(start)
        if not date_str:
            return None

        end = item.get("endDate") or ""
        _, end_time = self.parse_iso(end)

        loc = item.get("location") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        v_name = self.clean(loc.get("name")) or venue_name
        address = self.clean(
            loc.get("address") if isinstance(loc.get("address"), str)
            else (loc.get("address") or {}).get("streetAddress")
        )

        offers = item.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = None
        if isinstance(offers, dict):
            lo = offers.get("lowPrice") or offers.get("price")
            hi = offers.get("highPrice")
            currency = offers.get("priceCurrency", "USD")
            if lo is not None:
                price = f"${lo}" + (f"–${hi}" if hi else "")
                if lo == 0:
                    price = "Free"

        image = item.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, dict):
            image = image.get("url")

        return self.make_event(
            title=name,
            description=self.clean(item.get("description")),
            date=date_str,
            start_time=time_str,
            end_time=end_time,
            venue=v_name,
            address=address,
            url=item.get("url") or item.get("@id"),
            image_url=image,
            price=price,
            tags="venue",
        )

    def _parse_html_events(self, soup, venue_name: str, base_url: str) -> list[dict]:
        """Generic HTML fallback: find event cards with time[datetime] or .event classes."""
        events = []
        seen: set[str] = set()
        for card in soup.select(
            "article.event, .event-item, .show-item, li.event, "
            ".tribe-event, [class*='event-card'], [class*='show-card']"
        ):
            title_el = card.select_one("h1,h2,h3,h4,.event-title,.show-name,.tribe-event-title")
            if not title_el:
                continue
            title = self.clean(title_el.get_text())
            if not title or title in seen:
                continue
            seen.add(title)

            time_el = card.select_one("time")
            date_str, time_str = "", None
            if time_el:
                date_str, time_str = self.parse_iso(time_el.get("datetime", ""))
            if not date_str:
                continue

            link = card.select_one("a[href]")
            url = link.get("href") if link else None
            if url and not url.startswith("http"):
                # Make absolute
                from urllib.parse import urljoin
                url = urljoin(base_url, url)

            events.append(self.make_event(
                title=title,
                date=date_str,
                start_time=time_str,
                venue=venue_name,
                url=url,
                tags="venue",
            ))
        return events

    # ── Venue-specific parsers ───────────────────────────────────────────────

    def parse_casbah(self, soup, venue_name: str, base_url: str) -> list[dict]:
        """The Casbah uses a custom WordPress theme with .shows-list items."""
        events = []
        for item in soup.select(".shows-list-item, .show, article.show"):
            title_el = item.select_one(".show-title, h2, h3")
            if not title_el:
                continue
            title = self.clean(title_el.get_text())

            date_el = item.select_one("time, .show-date")
            date_str, time_str = "", None
            if date_el:
                dt = date_el.get("datetime") or date_el.get_text()
                date_str, time_str = self.parse_iso(dt)
            if not date_str:
                # Try parsing a human-readable date
                date_text = self.clean(item.select_one(".date, .show-date, time") and
                                       item.select_one(".date, .show-date, time").get_text())
                date_str = self._parse_human_date(date_text)
            if not date_str:
                continue

            link = item.select_one("a[href]")
            url = link.get("href") if link else None

            price_el = item.select_one(".price, .ticket-price, .show-price")
            price = self.clean(price_el.get_text()) if price_el else None

            img = item.select_one("img")
            image_url = img.get("src") if img else None

            events.append(self.make_event(
                title=title,
                date=date_str,
                start_time=time_str,
                venue=venue_name,
                url=url,
                price=price,
                image_url=image_url,
                tags="venue,music",
            ))
        # fallback
        if not events:
            events = self.parse_generic_json_ld(soup, venue_name, base_url)
        return events

    def parse_observatory(self, soup, venue_name: str, base_url: str) -> list[dict]:
        """Observatory North Park — Eventbrite-powered or custom."""
        # Try JSON-LD first
        events = self.parse_generic_json_ld(soup, venue_name, base_url)
        if events:
            return events
        # Fallback: look for .event-listing or similar
        return self._parse_html_events(soup, venue_name, base_url)

    def parse_hob(self, soup, venue_name: str, base_url: str) -> list[dict]:
        """House of Blues — uses Live Nation embed or JSON-LD."""
        events = self.parse_generic_json_ld(soup, venue_name, base_url)
        if not events:
            events = self._parse_html_events(soup, venue_name, base_url)
        return events

    def _parse_human_date(self, text: Optional[str]) -> str:
        if not text:
            return ""
        text = text.strip()
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
                    "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return ""
