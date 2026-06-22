"""
Social media event scrapers — Facebook & Instagram.

Facebook:
  - Public Facebook Events pages (no auth required for public events)
  - Uses Playwright to render the JS, then extracts structured JSON-LD
    or the event card HTML that Facebook renders for logged-out users.

Instagram:
  - Instagram doesn't have a public events API.
  - We scrape public venue/bar accounts for post text containing event keywords
    (date + event name patterns) using their public profile endpoint.
  - Requires Playwright because Instagram is JS-rendered.

VENUES_FACEBOOK and VENUES_INSTAGRAM below are curated lists of San Diego
bars, venues, and restaurants with active event pages.
Add entries as you discover new ones (or run discover.py to find candidates).
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, date
from typing import Optional

from .base import BaseScraper

# ── San Diego venue Facebook pages ───────────────────────────────────────────
# Format: (human_name, facebook_page_or_events_url)
VENUES_FACEBOOK = [
    ("The Casbah",             "https://www.facebook.com/thecasbahsandiego/events/"),
    ("Belly Up Tavern",        "https://www.facebook.com/bellyuptavern/events/"),
    ("Observatory North Park", "https://www.facebook.com/ObservatoryNorthPark/events/"),
    ("Soda Bar",               "https://www.facebook.com/SodaBarSD/events/"),
    ("The Irenic",             "https://www.facebook.com/theirenic/events/"),
    ("Music Box SD",           "https://www.facebook.com/MusicBoxSD/events/"),
    ("SOMA San Diego",         "https://www.facebook.com/somasandiego/events/"),
    ("House of Blues SD",      "https://www.facebook.com/houseofbluessandiego/events/"),
    ("The Quartyard",          "https://www.facebook.com/thequartyard/events/"),
    ("Whistle Stop Bar",       "https://www.facebook.com/whistlestopbar/events/"),
    ("Bar Pink",               "https://www.facebook.com/barpinksd/events/"),
    ("Blonde Bar",             "https://www.facebook.com/BlondeBarSD/events/"),
    ("Coin-Op SD",             "https://www.facebook.com/coinopsd/events/"),
    ("Fluxx Nightclub",        "https://www.facebook.com/fluxxnightclub/events/"),
    ("Bang Bang",              "https://www.facebook.com/bangbangsandiego/events/"),
    ("Spin Nightclub",         "https://www.facebook.com/spinnightclub/events/"),
    ("Kava Lounge",            "https://www.facebook.com/kavaloungsd/events/"),
    ("Space SD",               "https://www.facebook.com/SpaceSD/events/"),
    ("Analog Bar",             "https://www.facebook.com/analogbarsd/events/"),
    ("Prohibition Lounge",     "https://www.facebook.com/prohibitionsd/events/"),
    ("The Loft at UCSD",       "https://www.facebook.com/TheLoftUCSD/events/"),
    ("Soma Sidestage",         "https://www.facebook.com/somasidestage/events/"),
    ("Winston's Beach Club",   "https://www.facebook.com/WinstonsSanDiego/events/"),
    ("Brick by Brick",         "https://www.facebook.com/brickbybrick/events/"),
    ("4th & B",                "https://www.facebook.com/4thandb/events/"),
    ("The Venue SD",           "https://www.facebook.com/thevenuesd/events/"),
    ("San Diego Symphony",     "https://www.facebook.com/SanDiegoSymphony/events/"),
    ("Old Globe Theatre",      "https://www.facebook.com/oldglobetheatre/events/"),
    ("La Jolla Playhouse",     "https://www.facebook.com/lajollaplayhouse/events/"),
    ("Balboa Park",            "https://www.facebook.com/balboaparkofficial/events/"),
    ("Petco Park",             "https://www.facebook.com/padres/events/"),
    ("Snapdragon Stadium",     "https://www.facebook.com/snapdragonstadium/events/"),
    ("Del Mar Fairgrounds",    "https://www.facebook.com/SDFair/events/"),
]

# ── San Diego venue Instagram accounts ───────────────────────────────────────
VENUES_INSTAGRAM = [
    ("The Casbah",             "thecasbahsd"),
    ("Belly Up Tavern",        "bellyuptavern"),
    ("Observatory North Park", "observatorynorthpark"),
    ("Soda Bar",               "sodabarsd"),
    ("Music Box SD",           "musicboxsd"),
    ("SOMA San Diego",         "somasandiego"),
    ("Bang Bang",              "bangbangsandiego"),
    ("Fluxx Nightclub",        "fluxxnightclub"),
    ("Spin Nightclub",         "spinnightclub"),
    ("Space SD",               "spacesd"),
    ("Bar Pink",               "barpinksd"),
    ("Coin-Op SD",             "coinopsd"),
    ("The Quartyard",          "thequartyard"),
    ("Winston's Beach Club",   "winstonssandiego"),
    ("Brick by Brick",         "bricksandiego"),
    ("Whistle Stop Bar",       "whistlestopbar"),
]

# Regex to detect event-like mentions in IG captions
_DATE_RE = re.compile(
    r"(\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+\d{1,2}\b"
    r"|\b\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?\b"
    r"|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b)",
    re.I,
)
_EVENT_KW = re.compile(
    r"\b(show|concert|event|tonight|dj|live|performance|tour|ticket|doors|lineup|presents)\b",
    re.I,
)


class FacebookScraper(BaseScraper):
    name = "facebook"

    async def scrape(self) -> list[dict]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.logger.warning("Playwright not installed — skipping Facebook scraper")
            return []

        all_events: list[dict] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )

            for venue_name, fb_url in VENUES_FACEBOOK:
                try:
                    events = await self._scrape_page(ctx, venue_name, fb_url)
                    all_events.extend(events)
                    self.logger.debug(f"{venue_name}: {len(events)} events")
                except Exception as e:
                    self.logger.warning(f"{venue_name} ({fb_url}): {e}")
                await asyncio.sleep(1.5)  # polite rate limiting

            await browser.close()

        self.logger.info(f"Facebook total: {len(all_events)} events")
        return all_events

    async def _scrape_page(self, ctx, venue_name: str, url: str) -> list[dict]:
        page = await ctx.new_page()
        events: list[dict] = []
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2000)

            # FB renders events as JSON-LD on public /events pages for logged-out users
            ld_blocks = await page.evaluate("""
                () => [...document.querySelectorAll('script[type="application/ld+json"]')]
                       .map(s => s.textContent)
            """)
            for block in ld_blocks:
                try:
                    data = json.loads(block)
                except Exception:
                    continue
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") not in ("Event", "MusicEvent", "SocialEvent"):
                        continue
                    ev = self._from_json_ld(item, venue_name, url)
                    if ev:
                        events.append(ev)

            # Fallback: scrape visible event card text
            if not events:
                cards = await page.query_selector_all('[data-testid="event-card"], [role="article"]')
                for card in cards[:30]:
                    text = await card.inner_text()
                    ev = self._parse_card_text(text, venue_name)
                    if ev:
                        link_el = await card.query_selector("a[href*='/events/']")
                        if link_el:
                            ev["url"] = await link_el.get_attribute("href")
                        events.append(ev)
        finally:
            await page.close()
        return events

    def _from_json_ld(self, item: dict, venue_name: str, source_url: str) -> Optional[dict]:
        name = self.clean(item.get("name"))
        if not name:
            return None
        date_str, time_str = self.parse_iso(item.get("startDate", ""))
        if not date_str:
            return None
        loc = item.get("location") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        return self.make_event(
            title=name,
            description=self.clean(item.get("description")),
            date=date_str,
            start_time=time_str,
            venue=self.clean(loc.get("name")) or venue_name,
            address=self.clean(
                loc.get("address") if isinstance(loc.get("address"), str)
                else (loc.get("address") or {}).get("streetAddress")
            ),
            url=item.get("url") or source_url,
            image_url=(item.get("image") or [None])[0] if isinstance(item.get("image"), list) else item.get("image"),
        )

    def _parse_card_text(self, text: str, venue_name: str) -> Optional[dict]:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if len(lines) < 2:
            return None
        title = lines[0]
        if not _EVENT_KW.search(text) and not _DATE_RE.search(text):
            return None
        date_str = self._extract_date(text)
        if not date_str:
            return None
        return self.make_event(title=title, date=date_str, venue=venue_name)

    def _extract_date(self, text: str) -> str:
        m = _DATE_RE.search(text)
        if not m:
            return ""
        raw = m.group(0).strip()
        today = date.today()
        for fmt in ("%b %d", "%B %d", "%m/%d", "%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                d = datetime.strptime(raw, fmt)
                if "%Y" not in fmt and "%y" not in fmt:
                    d = d.replace(year=today.year)
                    if (today - d.date()).days > 180:
                        d = d.replace(year=today.year + 1)
                return d.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return ""


class InstagramScraper(BaseScraper):
    name = "instagram"

    async def scrape(self) -> list[dict]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.logger.warning("Playwright not installed — skipping Instagram scraper")
            return []

        all_events: list[dict] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Mobile/15E148 Safari/604.1"
                ),
            )
            for venue_name, handle in VENUES_INSTAGRAM:
                try:
                    events = await self._scrape_profile(ctx, venue_name, handle)
                    all_events.extend(events)
                    self.logger.debug(f"@{handle}: {len(events)} event posts")
                except Exception as e:
                    self.logger.warning(f"@{handle}: {e}")
                await asyncio.sleep(3)  # IG rate-limits aggressively

            await browser.close()

        self.logger.info(f"Instagram total: {len(all_events)} event posts")
        return all_events

    async def _scrape_profile(self, ctx, venue_name: str, handle: str) -> list[dict]:
        """
        Instagram's public profile page shows a grid of posts.
        We look for posts whose captions contain event keywords + a date.
        Only text is reliably accessible without auth.
        """
        page = await ctx.new_page()
        events: list[dict] = []
        url = f"https://www.instagram.com/{handle}/"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2500)

            # Extract alt text / aria-labels which often carry caption snippets
            captions = await page.evaluate("""
                () => [...document.querySelectorAll('img[alt]')]
                       .map(img => img.alt)
                       .filter(t => t && t.length > 20)
            """)

            # Also try to read any <meta> description
            meta_desc = await page.evaluate("""
                () => {
                    const m = document.querySelector('meta[name="description"]');
                    return m ? m.content : "";
                }
            """)
            if meta_desc:
                captions.append(meta_desc)

            for caption in captions[:40]:
                if not _EVENT_KW.search(caption) or not _DATE_RE.search(caption):
                    continue
                date_str = self._extract_date(caption)
                if not date_str:
                    continue
                # First line of caption is usually the event title / hook
                title_line = caption.split("\n")[0][:100].strip()
                if not title_line:
                    continue
                events.append(self.make_event(
                    title=title_line,
                    description=self.clean(caption[:300]),
                    date=date_str,
                    venue=venue_name,
                    url=url,
                ))
        finally:
            await page.close()
        return events

    def _extract_date(self, text: str) -> str:
        m = _DATE_RE.search(text)
        if not m:
            return ""
        raw = m.group(0).strip()
        today = date.today()
        for fmt in ("%b %d", "%B %d", "%m/%d", "%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                d = datetime.strptime(raw, fmt)
                if "%Y" not in fmt and "%y" not in fmt:
                    d = d.replace(year=today.year)
                    if (today - d.date()).days > 180:
                        d = d.replace(year=today.year + 1)
                return d.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return ""
