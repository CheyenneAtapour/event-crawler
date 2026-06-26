#!/usr/bin/env python3
"""
Master crawl script — run everything in order:

  1. Grow sources  — search DDG/Google for new SD venue URLs → sources.txt
  2. Venue discovery — search by venue category → sources.txt
  3. Scrape events  — pull events from all sources → events.db

Usage:
    python crawl.py            # full run (sources + events)
    python crawl.py --events   # skip source discovery, just scrape events
    python crawl.py --sources  # just grow sources, skip event scraping
    python crawl.py --vpn      # connect NordVPN before searching, disconnect after
"""
import sys
import asyncio
import logging
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

# Load .env before importing any scrapers so API keys are available
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("crawl")


def banner(text: str) -> None:
    width = 64
    print(f"\n{'═' * width}")
    print(f"  {text}")
    print(f"{'═' * width}\n")


def elapsed(start: float) -> str:
    s = int(time.monotonic() - start)
    return f"{s // 60}m {s % 60}s"


async def main(run_sources: bool = True, run_events: bool = True, use_vpn: bool = False) -> None:
    from db import init_db

    init_db()

    total_start = time.monotonic()

    # ── VPN connect ───────────────────────────────────────────────────────────
    if use_vpn and run_sources:
        banner("VPN — Connecting NordVPN for search requests")
        from vpn import connect
        connect()

    # ── Step 1: Grow sources.txt via web search ───────────────────────────────
    if run_sources:
        banner("STEP 1 — Growing sources via web search (Brave + Google + DDG)")
        t = time.monotonic()
        from scrapers.grow_sources import run_grow
        added = await run_grow(crawl_depth=1, use_vpn=use_vpn)
        logger.info(f"grow_sources done — {added} new sources added  [{elapsed(t)}]")

        # ── Step 2: Discover venues by category ───────────────────────────────
        banner("STEP 2 — Discovering venues by category (music, bars, comedy…)")
        t = time.monotonic()
        from scrapers.gmaps import run_gmaps_discovery
        added = await run_gmaps_discovery(use_vpn=use_vpn)
        logger.info(f"gmaps discovery done — {added} new sources added  [{elapsed(t)}]")

    # ── VPN disconnect ────────────────────────────────────────────────────────
    if use_vpn and run_sources:
        banner("VPN — Disconnecting NordVPN")
        from vpn import disconnect
        disconnect()

    # ── Step 3: Scrape events from all sources ────────────────────────────────
    if run_events:
        banner("STEP 3 — Scraping events from all sources → events.db")
        t = time.monotonic()
        from scrapers import run_all
        total = await run_all()
        logger.info(f"event scrape done — {total} events saved  [{elapsed(t)}]")

    # ── Step 4: Export to static JSON + push to GitHub Pages ─────────────────
    if run_events:
        banner("STEP 4 — Exporting to static JSON → GitHub Pages")
        t = time.monotonic()
        from export import export_to_json, push_to_github
        export_to_json()
        push_to_github()
        logger.info(f"export + push done  [{elapsed(t)}]")

    banner(f"ALL DONE  —  total time: {elapsed(total_start)}")


if __name__ == "__main__":
    args = set(sys.argv[1:])
    run_sources = "--events"  not in args
    run_events  = "--sources" not in args
    use_vpn     = "--vpn"     in args
    asyncio.run(main(run_sources=run_sources, run_events=run_events, use_vpn=use_vpn))
