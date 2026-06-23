"""
Dynamic venue scraper — reads all `venue` entries from sources.txt, scrapes
each for events, and also discovers + scrapes their social profiles (Facebook,
Instagram) for additional event listings.

Skips URLs scraped within the last 20 hours to avoid hammering sites nightly.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .base import BaseScraper
from .http import PoliteCrawler
from .venues import VenuesScraper
from .social import _session_file

logger = logging.getLogger(__name__)

SOURCES_FILE = Path(__file__).parent.parent.parent / "sources.txt"

# Paths that suggest a single event page rather than a listing
_SINGLE_EVENT_RE = re.compile(
    r"/e/[a-z0-9%-]+/?$"           # Eventbrite /e/event-slug
    r"|/events?/\d{5,}"            # /events/1234567 (numeric ID)
    r"|/tickets?/\d{5,}"           # /tickets/1234567
    r"|/calendar/\d{5,}"           # /calendar/1234567
    r"|/shows?/\d{5,}"             # /shows/1234567
    r"|/performances?/\d{4,}"      # /performances/1234
    r"|/collections/shows/?$"      # Shopify collections
    r"|[?&]event_id=\d+",
    re.I,
)

# Social profile URL patterns
_FB_PROFILE_RE  = re.compile(r"facebook\.com/(?!events/)([^/?#]+)/?$", re.I)
_IG_PROFILE_RE  = re.compile(r"instagram\.com/([a-z0-9_.]+)/?$", re.I)
_FB_EVENTS_RE   = re.compile(r"facebook\.com/([^/?#]+)/events/?", re.I)

# Link text / aria-label hints for social links
_SOCIAL_HINTS = re.compile(r"facebook|instagram|fb\.com|ig\.com", re.I)


def _load_venue_urls() -> list[tuple[str, str]]:
    """
    Read (name, url) pairs from sources.txt for venue-type entries,
    filtering out individual event page URLs.
    """
    if not SOURCES_FILE.exists():
        return []

    venues: list[tuple[str, str]] = []
    seen: set[str] = set()

    for line in SOURCES_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        typ, name, url = parts[0].strip(), parts[1].strip(), parts[2].strip()

        if typ != "venue":
            continue
        if not url.startswith("http"):
            continue
        if _SINGLE_EVENT_RE.search(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        venues.append((name, url))

    return venues


def _find_social_links(soup: BeautifulSoup, venue_url: str) -> dict[str, str]:
    """
    Scan a venue's homepage for Facebook and Instagram profile links.
    Returns {"facebook": url, "instagram": url} for whichever are found.
    """
    socials: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith("http"):
            continue

        if "facebook.com" in href and "facebook" not in socials:
            # Prefer /events page, fall back to profile
            if _FB_EVENTS_RE.search(href):
                socials["facebook"] = href
            elif _FB_PROFILE_RE.search(href):
                socials["facebook"] = href.rstrip("/") + "/events/"

        if "instagram.com" in href and "instagram" not in socials:
            if _IG_PROFILE_RE.search(href):
                socials["instagram"] = href

    return socials


class DynamicVenuesScraper(VenuesScraper):
    """
    Scrapes all venue URLs in sources.txt, plus each venue's Facebook
    and Instagram event pages.
    """
    name = "dynamic-venues"

    async def scrape(self) -> list[dict]:
        from db import get_stale_urls, mark_scraped

        venues = _load_venue_urls()
        if not venues:
            self.logger.info("No venue entries found in sources.txt")
            return []

        # Only scrape URLs not hit in the last 20 hours
        all_urls = [url for _, url in venues]
        stale = set(get_stale_urls(all_urls, min_age_hours=20))
        to_scrape = [(name, url) for name, url in venues if url in stale]

        self.logger.info(
            f"dynamic-venues: {len(venues)} total, {len(to_scrape)} stale (need scraping)"
        )

        if not to_scrape:
            return []

        all_events: list[dict] = []

        async with PoliteCrawler(min_delay=1.0, max_retries=2, backoff_base=2.0) as crawler:
            # Run venue scrapes concurrently — PoliteCrawler handles per-domain throttling
            tasks = [
                self._scrape_venue_full(name, url, crawler, mark_scraped)
                for name, url in to_scrape
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for (name, url), result in zip(to_scrape, results):
            if isinstance(result, Exception):
                self.logger.error(f"{name} ({url}): {result}")
            else:
                all_events.extend(result)

        self.logger.info(f"dynamic-venues: {len(all_events)} total events")
        return all_events

    async def _scrape_venue_full(
        self,
        name: str,
        url: str,
        crawler: PoliteCrawler,
        mark_scraped,
    ) -> list[dict]:
        """Scrape one venue: its website + any Facebook/Instagram pages found."""
        events: list[dict] = []

        # ── 1. Scrape the venue's own website ─────────────────────────────────
        soup, _ = await crawler.get_soup(url, verbose=False)
        if soup:
            venue_events = self.parse_generic_json_ld(soup, name, url)
            events.extend(venue_events)
            self.logger.debug(f"{name}: {len(venue_events)} events from site")

            # ── 2. Find social links on the venue page ─────────────────────────
            socials = _find_social_links(soup, url)
            social_events = await self._scrape_socials(name, socials, crawler)
            events.extend(social_events)

        mark_scraped(url, len(events))
        return events

    async def _scrape_socials(
        self,
        venue_name: str,
        socials: dict[str, str],
        crawler: PoliteCrawler,
    ) -> list[dict]:
        """Scrape Facebook events page and/or Instagram profile for a venue."""
        events: list[dict] = []

        fb_url = socials.get("facebook")
        ig_url = socials.get("instagram")

        if fb_url:
            fb_events = await self._scrape_facebook(venue_name, fb_url, crawler)
            events.extend(fb_events)
            self.logger.debug(f"{venue_name} FB: {len(fb_events)} events")

        if ig_url:
            ig_events = await self._scrape_instagram(venue_name, ig_url, crawler)
            events.extend(ig_events)
            self.logger.debug(f"{venue_name} IG: {len(ig_events)} events")

        return events

    async def _scrape_facebook(
        self, venue_name: str, url: str, crawler: PoliteCrawler
    ) -> list[dict]:
        """
        Scrape a Facebook /events page for JSON-LD event data.
        Facebook requires a logged-in session for most content, but public
        event pages do emit JSON-LD for bots/crawlers.
        Falls back to Playwright if the static fetch returns nothing.
        """
        import json

        soup, _ = await crawler.get_soup(url, verbose=False)
        if not soup:
            return await self._scrape_facebook_playwright(venue_name, url)

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
                    ev["source"] = "facebook"
                    events.append(ev)

        if not events:
            return await self._scrape_facebook_playwright(venue_name, url)
        return events

    async def _scrape_facebook_playwright(
        self, venue_name: str, url: str
    ) -> list[dict]:
        """Playwright fallback for Facebook event pages."""
        import json as _json
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return []

        events = []
        session = _session_file("facebook")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
                ),
                storage_state=session,
            )
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                await page.wait_for_timeout(2000)
                ld_blocks = await page.evaluate(
                    "() => [...document.querySelectorAll('script[type=\"application/ld+json\"]')]"
                    ".map(s => s.textContent)"
                )
                for block in ld_blocks:
                    try:
                        data = _json.loads(block)
                    except Exception:
                        continue
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if item.get("@type") not in ("Event", "MusicEvent", "SocialEvent"):
                            continue
                        ev = self._from_json_ld(item, venue_name)
                        if ev:
                            ev["source"] = "facebook"
                            events.append(ev)
            except Exception as e:
                self.logger.debug(f"FB Playwright {url}: {e}")
            finally:
                await browser.close()

        return events

    async def _scrape_instagram(
        self, venue_name: str, url: str, crawler: PoliteCrawler
    ) -> list[dict]:
        """
        Scrape Instagram captions for event mentions.
        Instagram requires Playwright — static fetch returns a shell page.
        """
        import re as _re

        _DATE_RE = _re.compile(
            r"(\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+\d{1,2}\b"
            r"|\b\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?\b"
            r"|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b)",
            _re.I,
        )
        _EVENT_KW = _re.compile(
            r"\b(show|concert|event|tonight|dj|live|performance|tour|ticket|doors|lineup|presents)\b",
            _re.I,
        )

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return []

        events = []
        session = _session_file("instagram")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"
                ),
                storage_state=session,
            )
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
                await page.wait_for_timeout(2500)
                captions = await page.evaluate(
                    "() => [...document.querySelectorAll('img[alt]')]"
                    ".map(img => img.alt).filter(t => t && t.length > 20)"
                )
                meta = await page.evaluate(
                    "() => { const m = document.querySelector('meta[name=\"description\"]');"
                    " return m ? m.content : ''; }"
                )
                if meta:
                    captions.append(meta)

                from datetime import datetime, date
                today = date.today()

                for caption in captions[:40]:
                    if not _EVENT_KW.search(caption) or not _DATE_RE.search(caption):
                        continue
                    m = _DATE_RE.search(caption)
                    raw = m.group(0).strip()
                    date_str = ""
                    for fmt in ("%b %d", "%B %d", "%m/%d", "%m-%d", "%m/%d/%Y"):
                        try:
                            d = datetime.strptime(raw, fmt)
                            if "%Y" not in fmt:
                                d = d.replace(year=today.year)
                                if (today - d.date()).days > 180:
                                    d = d.replace(year=today.year + 1)
                            date_str = d.strftime("%Y-%m-%d")
                            break
                        except ValueError:
                            continue
                    if not date_str:
                        continue
                    title = caption.split("\n")[0][:100].strip()
                    if not title:
                        continue
                    events.append(self.make_event(
                        title=title,
                        description=self.clean(caption[:300]),
                        date=date_str,
                        venue=venue_name,
                        url=url,
                        source="instagram",
                    ))
            except Exception as e:
                self.logger.debug(f"IG {url}: {e}")
            finally:
                await browser.close()

        return events
