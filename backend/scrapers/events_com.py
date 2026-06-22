"""
Eventbrite scraper for San Diego events.
Event data is server-rendered into window.__SERVER_DATA__ as a JSON blob
containing a jsonld array with full Event schema objects.
"""
from __future__ import annotations

import json
import re
from .base import BaseScraper

SEARCH_URL = "https://www.eventbrite.com/d/ca--san-diego/events/"
_SERVER_DATA_RE = re.compile(r"window\.__SERVER_DATA__\s*=\s*(\{.*?\});\s*\n", re.S)


class EventsComScraper(BaseScraper):
    name = "eventbrite"

    async def scrape(self) -> list[dict]:
        events = []

        for page in range(1, 4):
            url = f"{SEARCH_URL}?page={page}"
            soup = await self.fetch(url)
            if not soup:
                break

            page_events = self._parse_server_data(soup)
            if not page_events and page == 1:
                # Fallback: try the ItemList JSON-LD block
                page_events = self._parse_jsonld(soup)

            if not page_events:
                break

            events.extend(page_events)

            if page < 3:
                import asyncio
                await asyncio.sleep(1.5)

        self.logger.info(f"Eventbrite: {len(events)} events")
        return events

    def _parse_server_data(self, soup) -> list[dict]:
        """Extract events from window.__SERVER_DATA__ JSON blob."""
        events = []
        for script in soup.find_all("script"):
            text = script.string or ""
            if "__SERVER_DATA__" not in text:
                continue
            m = _SERVER_DATA_RE.search(text)
            if not m:
                # Try a more permissive extraction
                start = text.find("window.__SERVER_DATA__ = ") + len("window.__SERVER_DATA__ = ")
                if start < len("window.__SERVER_DATA__ = "):
                    continue
                # Find the end by counting braces
                depth, i, end = 0, start, start
                for i, ch in enumerate(text[start:], start):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                raw = text[start:end]
            else:
                raw = m.group(1)

            try:
                data = json.loads(raw)
            except Exception:
                continue

            # jsonld is a list of schema.org objects
            for item in data.get("jsonld", []):
                ev = self._from_schema(item)
                if ev:
                    events.append(ev)

            # Also check search_data.events array if present
            search = data.get("search_data", {}) or {}
            for item in (search.get("events", {}) or {}).get("results", []):
                ev = self._from_eb_result(item)
                if ev:
                    events.append(ev)

        return events

    def _parse_jsonld(self, soup) -> list[dict]:
        """Fallback: parse application/ld+json script tags."""
        events = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue
            items = []
            if isinstance(data, list):
                items = data
            elif data.get("@type") == "ItemList":
                items = [e.get("item", e) for e in data.get("itemListElement", [])]
            else:
                items = [data]
            for item in items:
                ev = self._from_schema(item)
                if ev:
                    events.append(ev)
        return events

    def _from_schema(self, item: dict) -> dict | None:
        if item.get("@type") not in ("Event", "MusicEvent", "SocialEvent"):
            return None
        date_str, time_str = self.parse_iso(item.get("startDate", ""))
        if not date_str:
            return None
        loc = item.get("location") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        offers = item.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = None
        if isinstance(offers, dict):
            lo = offers.get("lowPrice") or offers.get("price")
            if lo is not None:
                price = "Free" if str(lo) == "0" else f"${lo}"
        return self.make_event(
            title=self.clean(item.get("name")) or "",
            description=self.clean(item.get("description")),
            date=date_str,
            start_time=time_str,
            venue=self.clean(loc.get("name")),
            address=self.clean(
                loc.get("address") if isinstance(loc.get("address"), str)
                else (loc.get("address") or {}).get("streetAddress")
            ),
            url=item.get("url"),
            image_url=(item.get("image") or [None])[0]
                if isinstance(item.get("image"), list) else item.get("image"),
            price=price,
        )

    def _from_eb_result(self, item: dict) -> dict | None:
        """Parse Eventbrite's internal search result object."""
        start = (item.get("start_date") or "") + "T" + (item.get("start_time") or "")
        date_str, time_str = self.parse_iso(start.rstrip("T"))
        if not date_str:
            return None
        return self.make_event(
            title=self.clean(item.get("name")) or "",
            date=date_str,
            start_time=time_str,
            venue=self.clean((item.get("primary_venue") or {}).get("name")),
            url=item.get("url"),
            image_url=(item.get("image") or {}).get("url"),
            price="Free" if item.get("is_free") else None,
        )
