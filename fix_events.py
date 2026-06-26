#!/usr/bin/env python3
"""
Fix bad event dates in the DB by re-scraping sources that had date bugs,
then re-export to JSON and push to GitHub.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))


async def main():
    from db import init_db, get_conn, upsert_events

    init_db()

    # 1. Delete all sandiego.org events (had wrong dates from allDates_iso_date bug)
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM events WHERE source='sandiego.org'").fetchone()[0]
        conn.execute("DELETE FROM events WHERE source='sandiego.org'")
        print(f"Deleted {count} sandiego.org events with bad dates")

    # 2. Re-scrape sandiego.org with the fixed scraper
    print("\nRe-scraping sandiego.org...")
    from scrapers.sandiego_org import SanDiegoOrgScraper
    events = await SanDiegoOrgScraper().scrape()
    saved = upsert_events(events)
    print(f"Saved {saved} corrected sandiego.org events")

    # 3. Re-export and push
    print("\nExporting to JSON...")
    from export import export_to_json, push_to_github
    export_to_json()
    push_to_github()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
