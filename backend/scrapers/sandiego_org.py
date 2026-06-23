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
                    ev = self._parse_doc(doc)
                    if ev:
                        events.append(ev)

                self.logger.debug(f"Page {page}: {len(docs)} docs (total={total})")

                if page * PER_PAGE >= total or not docs:
                    break
                page += 1
                await asyncio.sleep(0.5)

        self.logger.info(f"sandiego.org: {len(events)} events")
        return events

    def _parse_doc(self, doc: dict) -> dict | None:
        def fv(key: str) -> str:
            v = doc.get("Fields", {}).get(key, {})
            if isinstance(v, dict):
                return v.get("Value") or ""
            return str(v) if v else ""

        def flist(key: str) -> list:
            v = doc.get("Fields", {}).get(key, {})
            if isinstance(v, dict):
                vals = v.get("Values") or []
                return [i.get("Value", "") for i in vals if isinstance(i, dict)]
            return []

        title = self.clean(fv("Title"))
        if not title:
            return None

        # allDates_iso_date holds ISO date strings; allDates_date holds Unix timestamps (skip)
        iso_field = doc.get("Fields", {}).get("allDates_iso_date", {})
        if not iso_field:
            return None

        # Values array has all occurrence dates; Value has the first/primary date
        all_dates: list[str] = []
        if isinstance(iso_field, dict):
            vals = iso_field.get("Values") or []
            all_dates = [v for v in vals if v and len(v) >= 8]
            if not all_dates:
                primary = iso_field.get("Value", "")
                if primary:
                    all_dates = [primary]

        if not all_dates:
            return None

        # Use the earliest upcoming date as primary
        date_str, time_str = self.parse_iso(all_dates[0])
        if not date_str:
            return None

        url = fv("Url")
        if url and not url.startswith("http"):
            url = BASE_URL + url

        # Images come as a JSON list
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
                        None
                    )
                    if image_url:
                        image_url = BASE_URL + image_url
            except Exception:
                pass

        neighborhood = self.clean(fv("Neighborhood"))
        description  = self.clean(fv("Description"))

        return self.make_event(
            title=title,
            description=description,
            date=date_str,
            start_time=time_str,
            venue=self.clean(fv("Event Venue") or fv("hero_headline")),
            city="San Diego",
            url=url,
            image_url=image_url,
            tags=neighborhood,
        )
