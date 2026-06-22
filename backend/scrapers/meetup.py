"""
Meetup.com — San Diego in-person events.

Meetup's public search page is React/Next.js rendered, so we use Playwright
to load the DOM, then intercept or parse the rendered event cards.
Falls back gracefully if playwright is not installed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from .base import BaseScraper

SEARCH_URL = (
    "https://www.meetup.com/find/events/"
    "?allMeetups=true&radius=25&userFreeform=San+Diego+CA&eventType=in-person"
)

# Meetup also exposes a GraphQL endpoint we can query directly
GRAPHQL_URL = "https://www.meetup.com/gql"
GQL_QUERY = """
query SearchEvents($input: SearchInput!) {
  results: searchEvents(input: $input) {
    pageInfo { endCursor hasNextPage }
    edges {
      node {
        id title dateTime endTime description
        venue { name address city }
        eventUrl
        images { baseUrl }
        feeSettings { amount currency }
      }
    }
  }
}
"""


class MeetupScraper(BaseScraper):
    name = "meetup"

    async def scrape(self) -> list[dict]:
        events = await self._scrape_graphql()
        if events:
            return events
        self.logger.info("GraphQL returned nothing, falling back to Playwright")
        return await self._scrape_playwright()

    # ── GraphQL path ────────────────────────────────────────────────────────

    async def _scrape_graphql(self) -> list[dict]:
        import httpx

        payload = {
            "query": GQL_QUERY,
            "variables": {
                "input": {
                    "query": "",
                    "lat": 32.7157,
                    "lon": -117.1611,
                    "radius": 40,
                    "eventType": "physical",
                    "numberOfEventsRequested": 200,
                }
            },
        }
        headers = {
            **{k: v for k, v in self._base_headers().items()},
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
            try:
                resp = await client.post(GRAPHQL_URL, json=payload)
                data = resp.json()
            except Exception as e:
                self.logger.warning(f"GraphQL request failed: {e}")
                return []

        edges = (
            data.get("data", {})
            .get("results", {})
            .get("edges", [])
        )
        if not edges:
            return []

        events = []
        for edge in edges:
            node = edge.get("node", {})
            date_str, time_str = self.parse_iso(node.get("dateTime", ""))
            if not date_str:
                continue

            venue_obj = node.get("venue") or {}
            venue = self.clean(venue_obj.get("name"))
            address = self.clean(venue_obj.get("address"))

            images = node.get("images") or []
            image_url = images[0].get("baseUrl") if images else None

            fee = node.get("feeSettings") or {}
            price = f"${fee['amount']} {fee['currency']}" if fee.get("amount") else "Free"

            events.append(self.make_event(
                title=self.clean(node.get("title")) or "",
                description=self.clean(node.get("description")),
                date=date_str,
                start_time=time_str,
                end_time=self.parse_iso(node.get("endTime", ""))[1],
                venue=venue,
                address=address,
                url=node.get("eventUrl"),
                image_url=image_url,
                price=price,
            ))

        self.logger.info(f"GraphQL: {len(events)} events")
        return events

    # ── Playwright fallback ─────────────────────────────────────────────────

    async def _scrape_playwright(self) -> list[dict]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.logger.warning("Playwright not installed — skipping Meetup scraper")
            return []

        events = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(user_agent=self._base_headers()["User-Agent"])
            page = await ctx.new_page()

            # Intercept the GraphQL responses that the page fires
            captured: list[dict] = []

            async def handle_response(response):
                if "gql" in response.url and response.status == 200:
                    try:
                        body = await response.json()
                        captured.append(body)
                    except Exception:
                        pass

            page.on("response", handle_response)

            try:
                await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
                # Give React time to render event cards after DOM loads
                await page.wait_for_timeout(4000)
            except Exception as e:
                self.logger.warning(f"Playwright navigation error: {e}")
            finally:
                await browser.close()

            for body in captured:
                edges = (
                    body.get("data", {})
                    .get("results", {})
                    .get("edges", [])
                )
                for edge in edges:
                    node = edge.get("node", {})
                    date_str, time_str = self.parse_iso(node.get("dateTime", ""))
                    if not date_str:
                        continue
                    events.append(self.make_event(
                        title=self.clean(node.get("title")) or "",
                        date=date_str,
                        start_time=time_str,
                        venue=self.clean((node.get("venue") or {}).get("name")),
                        url=node.get("eventUrl"),
                    ))

        self.logger.info(f"Playwright: {len(events)} events")
        return events

    def _base_headers(self) -> dict:
        from .base import HEADERS
        return HEADERS
