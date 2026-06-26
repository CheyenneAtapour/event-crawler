"""
Google Maps venue discovery for San Diego.

Google Maps doesn't have a public API for this use case without billing,
but their public web search results surface venue websites directly.
We query Google Maps-style searches (via DuckDuckGo) for venue categories
in San Diego and extract their official websites.

Adds discovered venues to sources.txt if they have event pages.

Run standalone:
    python -m scrapers.gmaps
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .http import PoliteCrawler
from .grow_sources import (
    SOURCES_FILE,
    HEADERS,
    append_to_sources,
    crawl_page,
    classify,
    score,
    load_existing_urls,
    _make_name,
    search_google,
    VPNSearcher,
)
from .search_state import SearchState

logger = logging.getLogger(__name__)

# ── Venue category search queries ─────────────────────────────────────────────
# These are formulated to surface venue websites via web search.
# We search for "{category} san diego" and extract official venue sites.
VENUE_CATEGORIES = [
    # Music
    "music venue san diego",
    "concert hall san diego",
    "live music bar san diego",
    "jazz club san diego",
    "blues bar san diego",
    "indie music venue san diego",
    "nightclub san diego",
    "dance club san diego",
    "electronic music club san diego",
    "latin nightclub san diego",
    # Food & drink
    "bar san diego events",
    "sports bar san diego",
    "rooftop bar san diego",
    "brewery san diego events",
    "wine bar san diego events",
    "cocktail bar san diego",
    "dive bar san diego",
    "karaoke bar san diego",
    "piano bar san diego",
    "speakeasy san diego",
    # Entertainment
    "comedy club san diego",
    "improv theater san diego",
    "escape room san diego",
    "arcade bar san diego",
    "bowling alley san diego events",
    "billiards bar san diego",
    "axe throwing san diego",
    "trivia night bar san diego",
    # Arts & culture
    "art gallery san diego events",
    "theater san diego",
    "playhouse san diego",
    "movie theater events san diego",
    "opera house san diego",
    "ballet san diego",
    # Outdoor & community
    "outdoor venue san diego events",
    "rooftop venue san diego",
    "park events san diego",
    "farmer market san diego",
    "flea market san diego",
    "food hall san diego events",
    # LGBTQ
    "gay bar san diego",
    "lgbtq nightclub san diego",
    "drag show bar san diego",
    # Large venues
    "arena san diego events",
    "amphitheater san diego",
    "stadium events san diego",
    "fairground san diego",
]

# Event page path patterns — if a venue site has these, it likely has events
EVENT_PATHS = re.compile(
    r"/(events?|calendar|shows?|lineup|tickets?|performances?|schedule|what.s.on)",
    re.I,
)


def _urls(results: list[tuple[str, str]]) -> list[str]:
    return [url for _, url in results if url]


async def find_events_page(base_url: str, client: PoliteCrawler) -> str | None:
    """
    Given a venue's homepage, try to find their events/calendar page.
    Returns the events page URL or None.
    """
    soup, _ = await client.get_soup(base_url, verbose=False)
    if not soup:
        return None

    # Look for internal links matching event path patterns
    parsed = urlparse(base_url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if EVENT_PATHS.search(href):
            if href.startswith("/"):
                return base_domain + href
            if href.startswith("http") and parsed.netloc in href:
                return href

    # If no events link found, check if the homepage itself has event content
    if soup.select("time[datetime], [class*=event], [class*=calendar], script[type='application/ld+json']"):
        return base_url

    return None


async def run_gmaps_discovery(
    categories: list[str] | None = None,
    min_score: int = 4,
    delay: float = 1.0,
    use_vpn: bool = False,
) -> int:
    """
    Discover San Diego venues via category searches, find their event pages,
    score them, and append to sources.txt.
    Returns number of new sources added.
    """
    categories = categories or VENUE_CATEGORIES
    existing_urls = load_existing_urls()
    found: list[tuple[int, str, str, str]] = []  # (score, type, title, url)
    seen: set[str] = set()

    async with PoliteCrawler(min_delay=delay, max_retries=3, backoff_base=2.0, max_connections=6) as client:

        print(f"\n{'─'*60}")
        print(f"GOOGLE MAPS DISCOVERY — {len(categories)} venue categories")
        print(f"{'─'*60}")
        state = SearchState()
        categories = state.prioritised(categories)
        print(f"  search state: {state.summary()}")
        searcher = VPNSearcher(use_vpn=use_vpn, state=state)

        for cat in categories:
            urls = _urls(await searcher.search(cat, client))

            for url in urls:
                url = url.rstrip("/")
                if not url or url in existing_urls or url in seen:
                    continue
                seen.add(url)

                typ = classify(url)
                if typ is None:
                    continue

                # Try to find their specific events page
                events_url = await find_events_page(url, client)
                target = (events_url or url).rstrip("/")

                if target in existing_urls or target in seen:
                    continue
                seen.add(target)

                soup, text = await client.get_soup(target, verbose=True)
                sc = score(target, soup, text)

                if sc >= min_score:
                    typ2 = classify(target) or typ
                    print(f"     → [{typ2}] score={sc} ✓")
                    found.append((sc, typ2, cat, target))
                else:
                    print(f"     → score={sc} (skip)")

    found.sort(reverse=True)
    print(f"\n{'─'*60}")
    print(f"WRITING — {len(found)} candidates to {SOURCES_FILE.name}")
    print(f"{'─'*60}")

    entries = [(_make_name(title, url, typ), typ, url) for _, typ, title, url in found]
    # swap to (type, name, url)
    new_entries = [(typ, name, url) for name, typ, url in entries]

    # dedupe
    seen_batch: set[str] = set()
    deduped = []
    for t, n, u in new_entries:
        if u not in seen_batch:
            seen_batch.add(u)
            deduped.append((t, n, u))

    added = append_to_sources(deduped, live=True)
    state.save()
    print(f"\n{'═'*60}")
    print(f"  DONE — {added} new venue sources added")
    print(f"  search state saved: {state.summary()}")
    print(f"{'═'*60}\n")
    return added


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(run_gmaps_discovery())
