"""
Polite async HTTP client used by all crawlers.

Features:
  - Per-domain rate limiting  (min gap between requests to the same host)
  - Retry with exponential backoff on transient failures
  - Retry-After header respect on 429 responses
  - Jitter to avoid thundering-herd when multiple domains unlock at once
  - Connection pool cap so we don't open hundreds of sockets
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .base import HEADERS

logger = logging.getLogger(__name__)

# Status codes worth retrying (server-side transient errors)
RETRY_STATUSES = {429, 500, 502, 503, 504}

# Exceptions that indicate a transient network problem
RETRY_EXCEPTIONS = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)


class PoliteCrawler:
    """
    Async HTTP client with per-domain throttling and automatic retries.

    Usage:
        async with PoliteCrawler(min_delay=1.0) as crawler:
            soup, text = await crawler.get_soup(url)
    """

    def __init__(
        self,
        min_delay: float = 1.0,      # minimum seconds between requests to the same domain
        max_retries: int = 3,
        backoff_base: float = 2.0,   # first retry waits ~2s, second ~4s, third ~8s
        timeout: float = 20.0,
        max_connections: int = 8,
        verbose: bool = True,
    ):
        self.min_delay = min_delay
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.verbose = verbose

        self._last_request: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_meta = asyncio.Lock()  # protects _locks dict creation
        # Domains that have returned 429 twice in this session get a cooldown
        self._domain_429_count: dict[str, int] = defaultdict(int)
        self._domain_blocked_until: dict[str, float] = defaultdict(float)
        self._MAX_429_BEFORE_BLOCK = 2
        self._BLOCK_DURATION = 300  # seconds (5 minutes)

        limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max(2, max_connections // 2),
        )
        self._client = httpx.AsyncClient(
            limits=limits,
            follow_redirects=True,
            timeout=httpx.Timeout(timeout),
            headers=HEADERS,
        )

    async def __aenter__(self) -> "PoliteCrawler":
        return self

    async def __aexit__(self, *_) -> None:
        await self._client.aclose()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _domain(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    async def _domain_lock(self, domain: str) -> asyncio.Lock:
        async with self._locks_meta:
            if domain not in self._locks:
                self._locks[domain] = asyncio.Lock()
            return self._locks[domain]

    def _is_blocked(self, domain: str) -> bool:
        return time.monotonic() < self._domain_blocked_until[domain]

    def _record_429(self, domain: str, url: str) -> bool:
        """
        Increment 429 counter for domain. If threshold hit, block it
        and return True so the caller can give up immediately.
        """
        self._domain_429_count[domain] += 1
        count = self._domain_429_count[domain]
        if count >= self._MAX_429_BEFORE_BLOCK:
            until = time.monotonic() + self._BLOCK_DURATION
            self._domain_blocked_until[domain] = until
            logger.warning(
                f"  {domain} returned 429 {count}x — skipping for "
                f"{self._BLOCK_DURATION // 60} min"
            )
            return True
        return False

    async def _throttle(self, domain: str) -> None:
        """Wait if we last hit this domain too recently."""
        elapsed = time.monotonic() - self._last_request[domain]
        gap = self.min_delay - elapsed
        if gap > 0:
            await asyncio.sleep(gap)

    # ── Public interface ──────────────────────────────────────────────────────

    async def get(self, url: str, **kwargs) -> Optional[httpx.Response]:
        """
        GET url with per-domain throttling and retry/backoff.
        Returns the Response, or None if all retries failed.
        """
        domain = self._domain(url)

        if self._is_blocked(domain):
            logger.debug(f"  {domain} is blocked — skipping {url[:60]}")
            return None

        lock = await self._domain_lock(domain)

        async with lock:
            await self._throttle(domain)

            for attempt in range(self.max_retries + 1):
                try:
                    resp = await self._client.get(url, **kwargs)
                    self._last_request[domain] = time.monotonic()

                    if resp.status_code == 429:
                        done = self._record_429(domain, url)
                        if done or attempt >= self.max_retries:
                            return resp   # caller sees the 429 but we stop retrying
                        wait = self._retry_wait(resp, attempt)
                        logger.debug(f"  [429] {url[:60]} — retry {attempt+1} in {wait:.1f}s")
                        await asyncio.sleep(wait)
                        continue

                    if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                        wait = self._retry_wait(resp, attempt)
                        logger.debug(f"  [{resp.status_code}] {url[:60]} — retry {attempt+1} in {wait:.1f}s")
                        await asyncio.sleep(wait)
                        continue

                    return resp

                except RETRY_EXCEPTIONS as exc:
                    self._last_request[domain] = time.monotonic()
                    if attempt < self.max_retries:
                        wait = self._backoff_wait(attempt)
                        logger.debug(f"  [{type(exc).__name__}] {url[:60]} — retry {attempt+1} in {wait:.1f}s")
                        await asyncio.sleep(wait)
                    else:
                        logger.debug(f"  [{type(exc).__name__}] {url[:60]} — giving up after {self.max_retries} retries")

            return None

    async def post(self, url: str, **kwargs) -> Optional[httpx.Response]:
        """POST with the same throttle + retry logic."""
        domain = self._domain(url)

        if self._is_blocked(domain):
            logger.debug(f"  {domain} is blocked — skipping POST {url[:60]}")
            return None

        lock = await self._domain_lock(domain)

        async with lock:
            await self._throttle(domain)

            for attempt in range(self.max_retries + 1):
                try:
                    resp = await self._client.post(url, **kwargs)
                    self._last_request[domain] = time.monotonic()

                    if resp.status_code == 429:
                        done = self._record_429(domain, url)
                        if done or attempt >= self.max_retries:
                            return resp
                        await asyncio.sleep(self._retry_wait(resp, attempt))
                        continue

                    if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                        await asyncio.sleep(self._retry_wait(resp, attempt))
                        continue

                    return resp

                except RETRY_EXCEPTIONS as exc:
                    self._last_request[domain] = time.monotonic()
                    if attempt < self.max_retries:
                        await asyncio.sleep(self._backoff_wait(attempt))
                    else:
                        logger.debug(f"  [{type(exc).__name__}] POST {url[:60]} — giving up")

            return None

    async def get_soup(
        self, url: str, verbose: bool | None = None
    ) -> tuple[Optional[BeautifulSoup], str]:
        """
        Fetch url and return (BeautifulSoup, raw_text).
        Returns (None, "") on failure.
        """
        show = self.verbose if verbose is None else verbose
        if show:
            short = url[:80] + ("…" if len(url) > 80 else "")
            print(f"  🌐 visiting: {short}")

        resp = await self.get(url)
        if resp is None:
            if show:
                print(f"     ✗ no response after retries")
            return None, ""

        if resp.status_code != 200:
            if show:
                print(f"     ✗ {resp.status_code}")
            # On hard auth/block failures, record as a domain 429 so we back off
            if resp.status_code in (401, 403):
                self._record_429(self._domain(url), url)
            return None, ""

        ct = resp.headers.get("content-type", "")
        if "text/html" not in ct:
            return None, ""

        soup = BeautifulSoup(resp.text, "lxml")
        return soup, resp.text

    # ── Backoff calculation ───────────────────────────────────────────────────

    def _backoff_wait(self, attempt: int) -> float:
        """Exponential backoff with ±20% jitter."""
        base = self.backoff_base * (2 ** attempt)
        return base + random.uniform(-base * 0.2, base * 0.2)

    def _retry_wait(self, resp: httpx.Response, attempt: int) -> float:
        """Respect Retry-After header if present, otherwise use backoff."""
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                return float(ra) + random.uniform(0, 1)
            except ValueError:
                pass
        return self._backoff_wait(attempt)
