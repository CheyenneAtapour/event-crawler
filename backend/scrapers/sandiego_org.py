"""
San Diego Tourism Authority events — sandiego.org/events-festivals

Uses the Cludo search API that the page calls internally.
The SiteKey is a public read-only search key embedded in the page JS.
Returns ~900+ events from the SDTA's official event aggregator.
"""
from __future__ import annotations

import asyncio
from datetime import date

import httpx

from .base import BaseScraper

CLUDO_URL  = "https://api-us1.cludo.com/api/v3/10001551/10002783/search"
SITE_KEY   = "SiteKey MTAwMDE1NTE6MTAwMDI3ODM6U2VhcmNoS2V5"
BASE_URL   = "https://www.sandiego.org"
PER_PAGE   = 100


class SanDiegoOrgScraper(BaseScraper):
    name = "sandiego.org"

    async def scrape(self) -> list[dict]:
        today = date.today().isoformat()
        events: list[dict] = []
        page = 1

        async with httpx.AsyncClient(
            headers={"Authorization": SITE_KEY, "Content-Type": "application/json"},
            follow_redirects=True,
            timeout=30,
        ) as client:
            while True:
                payload = {
                    "query": "*",
                    "perPage": PER_PAGE,
                    "page": page,
                    "sort": {"sticky": "desc", "sort_date_end": "asc"},
                    "enableFacetFiltering": True,
                    "facets": {},
                    "filters": {
                        "category_ids": ["7101"],
                        "content_type": ["event"],
                        "env": ["prod"],
                        "search_api_language": ["en"],
                        "date": ["event_filter_date", today, ""],
                        "status": ["1"],
                    },
                    "notFilters": {
                        "audience_ids": ["17536"],
                        "nid": ["57721"],
                    },
                }
                try:
                    resp = await client.post(CLUDO_URL, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:
                    self.logger.error(f"Page {page}: {e}")
                    break

                docs = data.get("TypedDocuments", [])
                total = data.get("TotalDocument", 0)

                for doc in docs:
                    events.extend(self._parse_doc(doc))

                self.logger.debug(f"Page {page}: {len(docs)} docs (total={total})")

                if page * PER_PAGE >= total or not docs:
                    break
                page += 1
                await asyncio.sleep(0.5)

        self.logger.info(f"sandiego.org: {len(events)} events")
        return events

    def _parse_doc(self, doc: dict) -> list[dict]:
        """
        Returns one dict per occurrence date (multi-day / recurring events get
        one calendar entry each).  Returns [] if the doc can't be parsed.
        """
        def fv(key: str) -> str:
            v = doc.get("Fields", {}).get(key, {})
            if isinstance(v, dict):
                return v.get("Value") or ""
            return str(v) if v else ""

        title = self.clean(fv("Title"))
        if not title:
            return []

        # event_filter_date = the actual upcoming occurrence date(s) matching our
        # date filter.  allDates_iso_date is the first-ever occurrence and is wrong
        # for recurring/multi-date events.
        filter_raw = fv("event_filter_date")
        if not filter_raw:
            return []

        # May be comma-separated for multi-date events ("2026-06-22, 2026-06-23")
        occurrence_dates = [d.strip() for d in filter_raw.split(",") if d.strip()]
        if not occurrence_dates:
            return []

        url = fv("Url")
        if url and not url.startswith("http"):
            url = BASE_URL + url

        # Images
        import json as _json
        images_raw = fv("preview_image_urls") or fv("images_urls")
        image_url = None
        if images_raw:
            try:
                imgs = _json.loads(images_raw) if isinstance(images_raw, str) else images_raw
                if imgs:
                    first = imgs[0] if isinstance(imgs, list) else imgs
                    urls = first.get("urls", {}) if isinstance(first, dict) else {}
                    image_url = next(
                        (v for v in urls.values() if v and isinstance(v, str) and v.startswith("/")),
                        None,
                    )
                    if image_url:
                        image_url = BASE_URL + image_url
            except Exception:
                pass

        neighborhood = self.clean(fv("Neighborhood"))
        description  = self.clean(fv("Description"))
        venue        = self.clean(fv("Event Venue") or fv("hero_headline"))

        # One calendar entry per occurrence date
        results = []
        for occ_date in occurrence_dates:
            date_str, time_str = self.parse_iso(occ_date)
            if not date_str:
                continue
            # Unique URL per occurrence so DB upsert doesn't collapse multi-day events
            occ_url = f"{url}#{occ_date}" if url and len(occurrence_dates) > 1 else url
            results.append(self.make_event(
                title=title,
                description=description,
                date=date_str,
                start_time=time_str,
                venue=venue,
                city="San Diego",
                url=occ_url,
                image_url=image_url,
                tags=neighborhood,
            ))
        return results
