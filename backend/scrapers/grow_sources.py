"""
Grow the sources list by:
  1. Searching DuckDuckGo for a bank of San Diego event queries
  2. Following result links and extracting further candidate URLs
  3. Scoring / classifying each candidate
  4. Appending new entries to sources.txt

Run standalone:
    python -m scrapers.grow_sources

Or trigger via API:
    POST /api/grow-sources
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote_plus
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .http import PoliteCrawler

logger = logging.getLogger(__name__)

SOURCES_FILE = Path(__file__).parent.parent.parent / "sources.txt"

# ── Search queries ────────────────────────────────────────────────────────────
QUERIES = [
    # Events / calendars
    "san diego events calendar",
    "san diego events this weekend",
    "san diego event venues list",
    "san diego local events blog",
    "things to do san diego",
    # Music / nightlife
    "san diego concerts calendar",
    "san diego live music bars",
    "san diego music venues",
    "san diego nightlife clubs",
    "san diego dj events",
    "san diego electronic music events",
    "san diego jazz clubs",
    "san diego indie music venue",
    "san diego hip hop events",
    "san diego latin nightclub",
    # Food & drink
    "san diego happy hour specials",
    "san diego bar events",
    "san diego brewery events",
    "san diego wine bar events",
    "san diego rooftop bar events",
    "san diego restaurant weekly events",
    "san diego food festival",
    "san diego popup food market",
    # Arts / culture
    "san diego comedy shows calendar",
    "san diego art gallery opening",
    "san diego theater calendar",
    "san diego film screenings",
    "san diego museum events",
    # Community / outdoor
    "san diego outdoor events",
    "san diego farmers market",
    "san diego community events",
    "san diego free events",
    "san diego trivia night",
    "san diego open mic night",
    "san diego karaoke bars",
    "san diego yoga events",
    "san diego social events groups",
    # LGBTQ / niche
    "san diego lgbtq events",
    "san diego pride events",
    "san diego sports bar events",
    "san diego drag shows",
    "san diego rave party",
    "san diego underground music",
]

# ── URL filters ───────────────────────────────────────────────────────────────
# Domains we skip entirely (search engines, wikis, social media main pages)
SKIP_DOMAINS = frozenset({
    "google.com", "google.co", "bing.com", "yahoo.com", "duckduckgo.com",
    "wikipedia.org", "wikimedia.org", "wikidata.org",
    "twitter.com", "x.com", "tiktok.com", "youtube.com", "pinterest.com",
    "reddit.com", "quora.com", "linkedin.com", "nextdoor.com",
    "maps.google.com", "apple.com", "microsoft.com", "amazon.com",
})

SD_RE = re.compile(
    r"san\s*diego|\bsd\b|619|858|"
    r"chula\s*vista|oceanside|escondido|el\s*cajon|la\s*jolla|"
    r"encinitas|carlsbad|santee|el\s*cajon|lemon\s*grove|"
    r"north\s*park|hillcrest|gaslamp|mission\s*hills|"
    r"pacific\s*beach|ocean\s*beach|mission\s*beach|"
    r"normal\s*heights|city\s*heights|kearny\s*mesa",
    re.I,
)

EVENT_RE = re.compile(
    r"event|calendar|show|concert|ticket|venue|nightlife|festival|"
    r"bar|club|music|comedy|trivia|happy.hour|lineup|gig|performance|"
    r"theater|theatre|gallery|screening|popup|market|brewery|"
    r"karaoke|open.mic|drag|rave|dj\b",
    re.I,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Type classification ───────────────────────────────────────────────────────

def classify(url: str) -> Optional[str]:
    """Return source type string, or None if URL should be skipped."""
    u = url.lower()
    parsed = urlparse(url)
    domain = parsed.netloc.lstrip("www.")

    # Hard skip
    for skip in SKIP_DOMAINS:
        if domain == skip or domain.endswith("." + skip):
            return None

    # Social — only allow specific paths
    if "facebook.com" in u:
        if "/events" in u or re.search(r"facebook\.com/[^/?#]+/?$", u):
            return "facebook"
        return None
    if "instagram.com" in u:
        if re.search(r"instagram\.com/[a-z0-9_.]+/?$", u, re.I):
            return "instagram"
        return None

    # Known aggregators
    for agg in ("eventbrite.com", "meetup.com", "ticketmaster.com",
                "axs.com", "livenation.com", "songkick.com",
                "bandsintown.com", "ra.co", "residentadvisor.net",
                "goldstar.com", "do-sandiego.com", "sandiego.eventful.com",
                "timeout.com", "yelp.com/events"):
        if agg in u:
            return "aggregator"

    # Neighborhood / city guides
    for nb in ("sandiego.org", "sdcitybeat.com", "sandiegomagazine.com",
               "sandiegoreader.com", "kpbs.org", "sandiegouniontribune.com",
               "northparkmainstreet.com", "hillcrestbia.com",
               "gaslampquarter.org", "downtownsandiego.org",
               "lajolla.com", "oceanbeachsandiego.com", "pacificbeachsandiego.com"):
        if nb in u:
            return "discover"

    # Default: venue or discover based on path
    if any(kw in u for kw in ("/events", "/calendar", "/shows", "/tickets", "/lineup")):
        return "venue"

    return "discover"


def score(url: str, soup: Optional[BeautifulSoup], page_text: str) -> int:
    """Relevance score: higher = more likely a useful SD event source."""
    s = 0
    u = url.lower()

    if EVENT_RE.search(u):
        s += 2
    if SD_RE.search(u):
        s += 3
    if any(kw in u for kw in ("/events", "/calendar", "/shows", "/lineup")):
        s += 2

    if soup:
        title = (soup.find("title") or soup.new_tag("t")).get_text()
        if EVENT_RE.search(title):
            s += 2
        if SD_RE.search(title):
            s += 2

        # JSON-LD with Event schema
        for script in soup.find_all("script", type="application/ld+json"):
            if '"Event"' in (script.string or ""):
                s += 4
                break

        # Calendar / event widgets
        if soup.select("time[datetime], .event, [class*=event], [class*=calendar], [class*=show]"):
            s += 2

    if SD_RE.search(page_text):
        s += 1

    return s


# ── Existing sources ──────────────────────────────────────────────────────────

def load_existing_urls() -> set[str]:
    """Return the set of URLs already in sources.txt."""
    if not SOURCES_FILE.exists():
        return set()
    urls: set[str] = set()
    for line in SOURCES_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            urls.add(parts[2].rstrip("/"))
    return urls


def load_existing_seeds() -> list[str]:
    """Return all URLs from sources.txt as crawl seeds."""
    if not SOURCES_FILE.exists():
        return []
    seeds: list[str] = []
    for line in SOURCES_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            seeds.append(parts[2].strip())
    return seeds


def append_to_sources(new_entries: list[tuple[str, str, str]], live: bool = True) -> int:
    """
    Append new (type, name, url) entries to sources.txt.
    Prints each addition as it happens. Returns count added.
    """
    if not new_entries:
        return 0

    existing = load_existing_urls()
    to_add = [(t, n, u) for t, n, u in new_entries if u.rstrip("/") not in existing]
    if not to_add:
        return 0

    by_type: dict[str, list[tuple[str, str, str]]] = {}
    for t, n, u in to_add:
        by_type.setdefault(t, []).append((t, n, u))

    lines: list[str] = [
        f"\n# ── Auto-discovered {time.strftime('%Y-%m-%d %H:%M')} "
        f"──────────────────────────────────────────────"
    ]
    for typ, entries in sorted(by_type.items()):
        for t, n, u in entries:
            line = f"{t:<12} | {n:<38} | {u}"
            lines.append(line)
            if live:
                print(f"  ✅ NEW SOURCE  [{t}] {n}")
                print(f"               {u}")

    with open(SOURCES_FILE, "a") as fh:
        fh.write("\n".join(lines) + "\n")

    return len(to_add)


# ── Search ────────────────────────────────────────────────────────────────────

async def search_google(query: str, client: PoliteCrawler) -> list[tuple[str, str]]:
    """
    Google Custom Search JSON API — 100 free queries/day.
    Requires GOOGLE_API_KEY and GOOGLE_CSE_ID in environment / .env
    Returns empty list if keys not set (DDG is used instead).
    """
    import os
    api_key = os.getenv("GOOGLE_API_KEY", "")
    cse_id  = os.getenv("GOOGLE_CSE_ID", "")
    if not api_key or not cse_id:
        return []

    print(f"  🔍 [Google] {query}")
    try:
        resp = await client.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": api_key, "cx": cse_id, "q": query, "num": 10},
        )
        data = resp.json()
        items = data.get("items", [])
        results = [(item.get("title", ""), item.get("link", "")) for item in items]
        print(f"     → {len(results)} results")
        return results
    except Exception as e:
        print(f"     ✗ Google failed: {e}")
        return []


class VPNSearcher:
    """
    Wraps search_all with automatic VPN rotation on consecutive zero-result queries.
    After ROTATE_AFTER empty searches in a row, rotates to a fresh NordVPN server
    and retries the current query once.
    """
    ROTATE_AFTER = 3   # consecutive zero-result queries before rotating

    def __init__(self):
        self._streak = 0

    async def search(self, query: str, client: PoliteCrawler) -> list[tuple[str, str]]:
        results = await search_all(query, client)

        if not results:
            self._streak += 1
            if self._streak >= self.ROTATE_AFTER:
                self._streak = 0
                rotated = self._rotate()
                if rotated:
                    print(f"  🔄 VPN rotated — retrying: {query}")
                    results = await search_all(query, client)
        else:
            self._streak = 0

        return results

    def _rotate(self) -> bool:
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from vpn import rotate
            return rotate()
        except Exception as e:
            print(f"  [VPN rotate failed: {e}]")
            return False


async def search_all(query: str, client: PoliteCrawler) -> list[tuple[str, str]]:
    """
    Run available search engines in priority order, merging and deduplicating.

    Priority:
      1. Google CSE  — needs GOOGLE_API_KEY + GOOGLE_CSE_ID  (100/day free)
      2. Brave       — needs BRAVE_API_KEY                   (2000/month free)
      3. DDG Lite    — no key, but often blocked by ISPs/VPNs
      4. DDG PW      — Playwright fallback for DDG, slowest

    If all fail (e.g. DDG blocked and no API keys set), returns [] gracefully
    and the crawler falls back to scraping already-known sources.
    Set at least BRAVE_API_KEY in .env for reliable results.
    """
    seen: set[str] = set()
    merged: list[tuple[str, str]] = []

    for fn in (search_google, search_brave, search_ddg_html, search_ddg_playwright):
        for title, url in await fn(query, client):
            if url and url not in seen:
                seen.add(url)
                merged.append((title, url))

    if not merged:
        print(f"     ⚠️  no search results — set BRAVE_API_KEY in .env for reliable searches")

    return merged


async def search_brave(query: str, client: PoliteCrawler) -> list[tuple[str, str]]:
    """
    Brave Search API — 2,000 free queries/month.
    Get a free key at https://api.search.brave.com/
    Set BRAVE_API_KEY in .env to enable.
    """
    import os
    api_key = os.getenv("BRAVE_API_KEY", "")
    if not api_key:
        return []

    print(f"  🔍 [Brave] {query}")
    try:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 20, "search_lang": "en", "country": "us"},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            },
        )
        if resp is None or resp.status_code != 200:
            print(f"     ✗ Brave {getattr(resp, 'status_code', 'no response')}")
            return []
        data = resp.json()
        results = [
            (r.get("title", ""), r.get("url", ""))
            for r in data.get("web", {}).get("results", [])
            if r.get("url")
        ]
        print(f"     → {len(results)} results")
        return results
    except Exception as e:
        print(f"     ✗ Brave error: {e}")
        return []


async def search_bing_rss(query: str, client: PoliteCrawler) -> list[tuple[str, str]]:
    """
    Bing RSS search — returns clean XML, no JS needed, no API key required.
    Reliably available and easy to parse.
    """
    import xml.etree.ElementTree as ET

    print(f"  🔍 [Bing] {query}")
    try:
        resp = await client.get(
            "https://www.bing.com/search",
            params={"q": query, "format": "rss"},
            headers={"User-Agent": HEADERS["User-Agent"]},
        )
        if resp is None or resp.status_code != 200:
            print(f"     ✗ Bing {getattr(resp, 'status_code', 'no response')}")
            return []

        root = ET.fromstring(resp.text)
        results = []
        for item in root.findall(".//item"):
            title_el = item.find("title")
            link_el  = item.find("link")
            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            url   = link_el.text.strip()  if link_el  is not None and link_el.text  else ""
            if url and url.startswith("http"):
                results.append((title, url))

        print(f"     → {len(results)} results")
        return results
    except Exception as exc:
        print(f"     ✗ Bing error: {type(exc).__name__}: {exc}")
        return []


async def search_ddg_playwright(query: str, _client: PoliteCrawler) -> list[tuple[str, str]]:
    """
    DDG Lite via Playwright — uses the minimal HTML version of DDG which loads
    faster and is much less likely to trigger bot detection than the full JS app.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []

    print(f"  🔍 [DDG/PW] {query}")
    results = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(user_agent=HEADERS["User-Agent"])
            page = await ctx.new_page()
            # Use DDG Lite — pure HTML, no JS, loads in ~1s, much harder to block
            await page.goto(
                f"https://lite.duckduckgo.com/lite/?q={query.replace(' ', '+')}&kl=us-en",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            # DDG Lite results: links inside <table> rows, class "result-link"
            links = await page.evaluate("""
                () => [...document.querySelectorAll('a.result-link, table tr td a[href^="http"]')]
                      .map(a => ({ title: (a.innerText || a.textContent).trim(), href: a.href }))
                      .filter(r => r.href && !r.href.includes('duckduckgo.com')
                                           && !r.href.includes('duck.com'))
            """)
            results = [(r["title"], r["href"]) for r in links if r.get("href")]
            await browser.close()
    except Exception as exc:
        print(f"     ✗ DDG/Playwright error: {type(exc).__name__}: {exc}")

    print(f"     → {len(results)} results")
    return results


async def search_ddg_html(query: str, client: PoliteCrawler) -> list[tuple[str, str]]:
    """
    DDG Lite via plain HTTP — fastest path, no JS needed.
    Uses a fresh httpx session each call to avoid session-cookie tracking.
    Falls back silently if blocked.
    """
    import httpx as _httpx

    print(f"  🔍 [DDG/Lite] {query}")
    try:
        async with _httpx.AsyncClient(follow_redirects=True, timeout=15) as fresh:
            resp = await fresh.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query, "kl": "us-en"},
                headers={"User-Agent": HEADERS["User-Agent"]},
            )
        if resp.status_code != 200:
            print(f"     ✗ {resp.status_code}")
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        results = []
        # DDG Lite: result links are plain <a> tags in a table, no special class
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                continue
            if "duckduckgo.com" in href or "duck.com" in href:
                continue
            title = a.get_text(strip=True)
            if title:
                results.append((title, href))
        print(f"     → {len(results)} results")
        await asyncio.sleep(2)   # brief pause between Lite calls
        return results
    except Exception as exc:
        print(f"     ✗ DDG/Lite error: {type(exc).__name__}: {exc}")
        return []


# ── Page crawl ────────────────────────────────────────────────────────────────

async def crawl_page(url: str, client: PoliteCrawler, verbose: bool = False) -> tuple[Optional[BeautifulSoup], str]:
    return await client.get_soup(url, verbose=verbose)


def extract_candidate_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Pull outbound links that look event-related."""
    candidates = []
    base_domain = urlparse(base_url).netloc
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0].strip()
        if not href:
            continue
        abs_url = urljoin(base_url, href) if not href.startswith("http") else href
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue
        # Skip same-domain links unless they look like event pages
        if parsed.netloc == base_domain:
            if not any(kw in abs_url.lower() for kw in ("/event", "/calendar", "/show", "/ticket")):
                continue
        if EVENT_RE.search(abs_url) or SD_RE.search(abs_url):
            candidates.append(abs_url)
    return candidates


# ── Main orchestration ────────────────────────────────────────────────────────

async def run_grow(
    queries: Optional[list[str]] = None,
    crawl_depth: int = 1,
    min_score: int = 4,
    delay: float = 1.2,
) -> int:
    """
    Run the grow cycle. Returns number of new sources added.

    crawl_depth=1 means: search → follow result links once.
    crawl_depth=2 means: also follow links found on those pages.
    """
    queries = queries or QUERIES
    existing_urls = load_existing_urls()
    existing_seeds = load_existing_seeds()
    candidates: dict[str, tuple[str, str]] = {}  # url → (type, title)

    async with PoliteCrawler(min_delay=delay, max_retries=3, backoff_base=2.0) as client:

        # ── Phase 1: search ───────────────────────────────────────────────────
        print(f"\n{'─'*60}")
        print(f"PHASE 1 — Web search ({len(queries)} queries)")
        print(f"{'─'*60}")
        search_urls: list[tuple[str, str]] = []
        searcher = VPNSearcher()

        for q in queries:
            results = await searcher.search(q, client)
            search_urls.extend(results)

        print(f"\n  → {len(search_urls)} total search result URLs")

        # ── Phase 2: seed crawl (existing sources.txt entries) ────────────────
        print(f"\n{'─'*60}")
        print(f"PHASE 2 — Crawling {len(existing_seeds)} existing sources for new links")
        print(f"{'─'*60}")
        seed_links: list[str] = []
        for seed_url in existing_seeds[:60]:
            soup, _ = await crawl_page(seed_url, client, verbose=True)
            if soup:
                links = extract_candidate_links(soup, seed_url)
                if links:
                    print(f"     → {len(links)} candidate links found")
                seed_links.extend(links)

        print(f"\n  → {len(seed_links)} total candidate links from seeds")

        all_candidates: list[tuple[str, str]] = search_urls + [(u, u) for u in seed_links]

        # ── Phase 3: score & classify candidates ──────────────────────────────
        print(f"\n{'─'*60}")
        print(f"PHASE 3 — Scoring {len(all_candidates)} candidate URLs")
        print(f"{'─'*60}")
        scored: list[tuple[int, str, str, str]] = []

        seen_in_run: set[str] = set()
        for title, url in all_candidates:
            url = url.rstrip("/")
            if not url or url in existing_urls or url in seen_in_run:
                continue
            seen_in_run.add(url)

            typ = classify(url)
            if typ is None:
                continue

            if typ in ("facebook", "instagram"):
                sc = 6  # always relevant, skip heavy fetch
                print(f"  🌐 visiting: {url[:80]}")
                print(f"     → social [{typ}] score=6")
            else:
                soup, text = await crawl_page(url, client, verbose=True)
                sc = score(url, soup, text)
                if sc >= min_score:
                    print(f"     → [{typ}] score={sc} ✓")
                else:
                    print(f"     → score={sc} (skip)")

            if sc >= min_score:
                scored.append((sc, typ, title or url, url))

        scored.sort(reverse=True)
        print(f"\n  → {len(scored)} candidates passed threshold (score ≥ {min_score})")

        # ── Phase 4: follow top candidates one level deeper ───────────────────
        if crawl_depth >= 2 and scored:
            print(f"\n{'─'*60}")
            print(f"PHASE 4 — Deep crawl: following top {min(20,len(scored))} candidates")
            print(f"{'─'*60}")
            top_urls = [url for _, _, _, url in scored[:20]]
            deep_links: list[str] = []
            for url in top_urls:
                soup, _ = await crawl_page(url, client, verbose=True)
                if soup:
                    links = extract_candidate_links(soup, url)
                    if links:
                        print(f"     → {len(links)} deep links")
                    deep_links.extend(links)

            for link in deep_links:
                link = link.rstrip("/")
                if link in existing_urls or link in seen_in_run:
                    continue
                seen_in_run.add(link)
                typ = classify(link)
                if typ is None:
                    continue
                soup, text = await crawl_page(link, client, verbose=True)
                sc = score(link, soup, text)
                if sc >= min_score:
                    print(f"     → [{typ}] score={sc} ✓")
                    scored.append((sc, typ, link, link))

            scored.sort(reverse=True)

    # ── Phase 5: write to sources.txt ─────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"PHASE 5 — Writing new sources to {SOURCES_FILE.name}")
    print(f"{'─'*60}")

    new_entries = [(typ, _make_name(title, url, typ), url) for _, typ, title, url in scored]

    seen_batch: set[str] = set()
    deduped = []
    for t, n, u in new_entries:
        if u not in seen_batch:
            seen_batch.add(u)
            deduped.append((t, n, u))

    added = append_to_sources(deduped, live=True)
    print(f"\n{'═'*60}")
    print(f"  DONE — {added} new sources added to {SOURCES_FILE.name}")
    print(f"{'═'*60}\n")
    return added


def _make_name(title: str, url: str, typ: str) -> str:
    """Derive a short human-readable name."""
    if title and title != url and len(title) < 80:
        # Strip trailing domain noise from search result titles
        name = re.sub(r"\s*[|\-–]\s*(san diego|events?|calendar).*$", "", title, flags=re.I)
        return name.strip()[:50] or _name_from_url(url)
    return _name_from_url(url)


def _name_from_url(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lstrip("www.")
    # For FB/IG, use the handle
    if "facebook.com" in url or "instagram.com" in url:
        parts = [p for p in parsed.path.split("/") if p and p not in ("events", "p")]
        if parts:
            return parts[0].replace(".", " ").replace("-", " ").title()
    return domain.split(".")[0].replace("-", " ").title()


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    depth = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    added = asyncio.run(run_grow(crawl_depth=depth))
    print(f"\n✓ {added} new sources added to {SOURCES_FILE}")
