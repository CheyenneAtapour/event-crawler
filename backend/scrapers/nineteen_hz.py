"""
19hz.info — SoCal electronic music calendar.
San Diego events live on the LA/SoCal page.
Table row format: [date_human | event@venue | genre | price | promoter | extra_link | date_iso]
"""
from __future__ import annotations

import re
from typing import Optional

from .base import BaseScraper

URL = "https://www.19hz.info/eventlisting_LosAngeles.php"

SD_PATTERN = re.compile(
    r"san diego|\bsd\b|barona|north park|gaslamp|casbah|chula vista|la jolla"
    r"|ocean beach|pacific beach|mission beach|kearny mesa|mira mesa|linda vista",
    re.I,
)

# Parse "(City)" out of "Event Name @ Venue (City)"
VENUE_CITY_RE = re.compile(r"^(.*?)\s*\(([^)]+)\)\s*$")

# Parse start time like "8pm", "12pm-4pm", "8pm-2am"
TIME_RE = re.compile(r"\((\d{1,2}(?::\d{2})?(?:am|pm))", re.I)


class NineteenHzScraper(BaseScraper):
    name = "19hz"

    async def scrape(self) -> list[dict]:
        soup = await self.fetch(URL)
        if not soup:
            return []

        events: list[dict] = []

        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 7:
                continue

            # Cell 6 has the date as YYYY/MM/DD — most reliable
            raw_date = self.clean(cells[6].get_text())
            if not raw_date:
                continue
            date_str = raw_date.replace("/", "-")  # 2026/06/21 → 2026-06-21
            if not re.match(r"\d{4}-\d{2}-\d{2}", date_str):
                continue

            # Cell 1: '<a href="...">Event Title</a> @ Venue (City)'
            name_cell = cells[1]
            link = name_cell.find("a")
            url = link.get("href") if link else None
            if url and not url.startswith("http"):
                url = "https://www.19hz.info/" + url.lstrip("/")
            # Must use full cell text, not just link text, to capture "@ Venue (City)"
            full_text = self.clean(name_cell.get_text()) or ""

            # Filter for San Diego
            if not SD_PATTERN.search(full_text):
                continue

            # Split "Event @ Venue (City)" → event_title, venue
            if "@" in full_text:
                title_part, venue_part = full_text.split("@", 1)
                title = self.clean(title_part) or full_text
                # Strip city from venue: "Music Box (San Diego)" → "Music Box"
                m = VENUE_CITY_RE.match(venue_part.strip())
                venue = self.clean(m.group(1)) if m else self.clean(venue_part)
            else:
                title = full_text
                venue = None

            # Cell 0: "Sun: Jun 21 (8pm)" — extract time
            time_raw = self.clean(cells[0].get_text()) or ""
            tm = TIME_RE.search(time_raw)
            start_time = self._parse_time(tm.group(1)) if tm else None

            genre = self.clean(cells[2].get_text())
            price = self.clean(cells[3].get_text())
            promoter = self.clean(cells[4].get_text())

            events.append(self.make_event(
                title=title or full_text,
                date=date_str,
                start_time=start_time,
                venue=venue,
                city="San Diego",
                url=url,
                price=price,
                tags=genre,
                description=f"Promoter: {promoter}" if promoter else None,
            ))

        self.logger.info(f"Found {len(events)} San Diego events")
        return events

    def _parse_time(self, raw: str) -> Optional[str]:
        raw = raw.strip().lower()
        try:
            import re as _re
            m = _re.match(r"(\d{1,2})(?::(\d{2}))?(am|pm)", raw)
            if not m:
                return None
            h, mins, ampm = m.groups()
            h = int(h)
            mins = int(mins) if mins else 0
            if ampm == "pm" and h != 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
            return f"{h:02d}:{mins:02d}"
        except Exception:
            return None
