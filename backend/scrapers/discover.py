"""
Source discovery crawler.

Finds new San Diego event sources we don't yet scrape by:
1. Querying a curated seed list of known aggregators / venue types
2. Following links from those pages to discover additional event sites
3. Scoring each candidate URL for event-page signals
4. Writing candidates to discovered_sources.json for human review

Run standalone:
    python -m scrapers.discover
"""
from __future__ import annotations
import asyncio
import json
import re
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

OUTPUT = Path(__file__).parent.parent.parent / "discovered_sources.json"

# ── Seed URLs ────────────────────────────────────────────────────────────────
# Known San Diego event aggregators, venue directories, and listing sites.
# Add more seeds here to widen the net.
SEEDS = [
    # Aggregators & calendars
    "https://sandiego.eventful.com/events",
    "https://www.sandiego.org/articles/events.aspx",
    "https://www.sdcitybeat.com/music/live-music-calendar/",
    "https://www.sandiegomagazine.com/things-to-do/events/",
    "https://www.yelp.com/events/san-diego-ca",
    "https://www.facebook.com/events/explore/sandiego/",
    "https://www.timeout.com/san-diego/things-to-do/san-diego-events-calendar",
    "https://www.eventbrite.com/d/ca--san-diego/events/",
    "https://www.goldstar.com/events/cities/san-diego-ca",
    "https://www.downtownsandiego.org/events/",
    "https://www.sdfoodandbeverage.com/events/",
    "https://www.signonsandiego.com/things-to-do/events/",
    "https://www.thisdaylive.com/index.cfm/bandindex/region/SD/",
    "https://bandsintown.com/a/local-shows?location=san+diego%2Cca",
    "https://songkick.com/metro-areas/26330-us-san-diego",
    "https://ra.co/clubs/us/sandiego",
    "https://residentadvisor.net/events/us/sandiego",
    "https://www.ticketmaster.com/san-diego-california-tickets/city/KovZpZAedFtA",
    "https://www.axs.com/events?q=san+diego",
    "https://www.livenation.com/discover/city/san-diego",
    # Neighborhood / community
    "https://www.northparkmainstreet.com/events/",
    "https://www.hillcrestbia.com/events/",
    "https://www.gaslampquarter.org/events/",
    "https://www.lajolla.com/events/",
    "https://www.oceanbeachsandiego.com/events/",
    "https://www.pacificbeachsandiego.com/events/",
    # Venue directories
    "https://www.sdbg.org/",       # SD Botanic Garden
    "https://www.sdmoa.org/",      # Museum of Art
    "https://www.sdnat.org/",      # Natural History Museum
    "https://www.balboapark.org/events",
    "https://www.sandiegozoo.org/events/",
    "https://www.pechanga.com/entertainment/upcoming-events",
    # Niche / scene-specific
    "https://www.19hz.info/",
    "https://www.sandiegoreader.com/events/",
    "https://www.kpbs.org/events/",
    "https://www.sandiegouniontribune.com/things-to-do",
    "https://afrotech.com/events?city=san-diego",
    "https://www.latinosindiego.org/events",
]

# Signals that a page is an event listing
EVENT_SIGNALS = re.compile(
    r"(event|calendar|shows?|concerts?|gigs?|tickets?|lineup|festival|nightlife|venue)",
    re.I,
)

# San Diego geo signals
SD_SIGNALS = re.compile(
    r"san\s+diego|SD\b|619|858|chula\s+vista|oceanside|escondido|el\s+cajon",
    re.I,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SDEventBot/1.0; +https://github.com/sdbot)"
    ),
    "Accept": "text/html,*/*;q=0.8",
}

MAX_DEPTH   = 2    # how many hops to follow from seeds
MAX_LINKS   = 30   # max links to follow per page
SCORE_THRESHOLD = 3


async def score_url(url: str, soup: BeautifulSoup | None, text: str) -> int:
    """Return a relevance score for a candidate URL + page content."""
    score = 0
    u = url.lower()

    # URL signals
    if EVENT_SIGNALS.search(u):
        score += 2
    if SD_SIGNALS.search(u):
        score += 2
    if any(kw in u for kw in ["/calendar", "/events", "/shows", "/tickets"]):
        score += 2

    if soup:
        # Title / heading signals
        for tag in soup.select("title, h1, h2"):
            if EVENT_SIGNALS.search(tag.get_text()):
                score += 1
            if SD_SIGNALS.search(tag.get_text()):
                score += 1

        # Structured data
        if soup.find("script", type="application/ld+json"):
            ld = soup.find("script", type="application/ld+json").string or ""
            if '"Event"' in ld or '"MusicEvent"' in ld:
                score += 3

        # Common event list elements
        if soup.select("time[datetime], .event, .show, [class*=event], [class*=calendar]"):
            score += 2

    return score


async def crawl_seed(client: httpx.AsyncClient, seed_url: str, visited: set, depth: int) -> list[dict]:
    if seed_url in visited or depth > MAX_DEPTH:
        return []
    visited.add(seed_url)

    try:
        resp = await client.get(seed_url, timeout=20)
        resp.raise_for_status()
        text = resp.text
        soup = BeautifulSoup(text, "lxml")
    except Exception as e:
        logger.debug(f"Skip {seed_url}: {e}")
        return []

    score = await score_url(seed_url, soup, text)
    results = []

    if score >= SCORE_THRESHOLD:
        results.append({
            "url":    seed_url,
            "score":  score,
            "title":  (soup.find("title") or soup.new_tag("t")).get_text().strip()[:120],
            "depth":  depth,
        })

    if depth < MAX_DEPTH:
        # Harvest promising child links
        base = f"{urlparse(seed_url).scheme}://{urlparse(seed_url).netloc}"
        links_seen = 0
        for a in soup.find_all("a", href=True)[:200]:
            href = a["href"].split("#")[0].strip()
            if not href:
                continue
            abs_url = urljoin(base, href) if not href.startswith("http") else href
            parsed  = urlparse(abs_url)
            if parsed.scheme not in ("http", "https"):
                continue
            if abs_url in visited:
                continue
            # Only follow links that look event-related
            if not EVENT_SIGNALS.search(abs_url) and not SD_SIGNALS.search(abs_url):
                continue
            links_seen += 1
            if links_seen > MAX_LINKS:
                break
            child = await crawl_seed(client, abs_url, visited, depth + 1)
            results.extend(child)

    return results


async def run_discovery() -> list[dict]:
    visited: set[str] = set()
    all_results: list[dict] = []

    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, limits=limits) as client:
        tasks = [crawl_seed(client, seed, visited, depth=0) for seed in SEEDS]
        batches = await asyncio.gather(*tasks, return_exceptions=True)

    for batch in batches:
        if isinstance(batch, list):
            all_results.extend(batch)

    # Deduplicate by URL, keep highest score
    seen: dict[str, dict] = {}
    for r in all_results:
        u = r["url"]
        if u not in seen or r["score"] > seen[u]["score"]:
            seen[u] = r

    ranked = sorted(seen.values(), key=lambda x: -x["score"])

    OUTPUT.write_text(json.dumps(ranked, indent=2))
    logger.info(f"Discovery complete — {len(ranked)} candidates written to {OUTPUT}")
    return ranked


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    results = asyncio.run(run_discovery())
    print(f"\nTop 20 candidates:")
    for r in results[:20]:
        print(f"  [{r['score']:2d}] {r['url']}")
        if r.get("title"):
            print(f"       {r['title']}")
