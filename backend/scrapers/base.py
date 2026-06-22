from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional, Union

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class BaseScraper:
    name = "base"

    def __init__(self):
        self.logger = logging.getLogger(f"scraper.{self.name}")

    async def scrape(self) -> list[dict]:
        raise NotImplementedError

    async def fetch(self, url: str, **kwargs) -> Optional[BeautifulSoup]:
        async with httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=30
        ) as client:
            try:
                resp = await client.get(url, **kwargs)
                resp.raise_for_status()
                return BeautifulSoup(resp.text, "lxml")
            except Exception as e:
                self.logger.error(f"fetch {url}: {e}")
                return None

    async def fetch_json(self, url: str, **kwargs) -> Optional[Union[dict, list]]:
        async with httpx.AsyncClient(
            headers={**HEADERS, "Accept": "application/json"},
            follow_redirects=True,
            timeout=30,
        ) as client:
            try:
                resp = await client.get(url, **kwargs)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                self.logger.error(f"fetch_json {url}: {e}")
                return None

    def make_event(self, **kw) -> dict:
        return {
            "title":       kw.get("title", ""),
            "description": kw.get("description"),
            "date":        kw.get("date", ""),
            "start_time":  kw.get("start_time"),
            "end_time":    kw.get("end_time"),
            "venue":       kw.get("venue"),
            "address":     kw.get("address"),
            "city":        kw.get("city", "San Diego"),
            "url":         kw.get("url"),
            "source":      self.name,
            "image_url":   kw.get("image_url"),
            "price":       kw.get("price"),
            "tags":        kw.get("tags"),
        }

    @staticmethod
    def parse_iso(dt_str: str) -> "tuple[str, Optional[str]]":
        """Parse ISO datetime string → (date, time) tuple."""
        if not dt_str:
            return "", None
        try:
            dt_str = dt_str.replace("Z", "+00:00")
            d = datetime.fromisoformat(dt_str)
            return d.strftime("%Y-%m-%d"), d.strftime("%H:%M")
        except ValueError:
            return "", None

    @staticmethod
    def clean(text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        return re.sub(r"\s+", " ", text).strip() or None
