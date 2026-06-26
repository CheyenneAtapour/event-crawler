"""
Persists search query results between runs so zero-result queries
get prioritised on the next crawl.

State file: backend/search_state.json
Format:
  {
    "san diego comedy club": {
      "last_attempt":  "2026-06-24T09:00:00",
      "last_success":  "2026-06-23T10:00:00",  // null if never succeeded
      "failures":      3                         // consecutive zero-result runs
    },
    ...
  }
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent.parent / "search_state.json"


class SearchState:
    def __init__(self) -> None:
        self._data: dict[str, dict] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if STATE_FILE.exists():
            try:
                self._data = json.loads(STATE_FILE.read_text())
            except Exception as e:
                logger.warning(f"search_state: could not load {STATE_FILE}: {e}")
                self._data = {}

    def save(self) -> None:
        try:
            STATE_FILE.write_text(json.dumps(self._data, indent=2))
        except Exception as e:
            logger.warning(f"search_state: could not save: {e}")

    # ── Record a result ───────────────────────────────────────────────────────

    def record(self, query: str, result_count: int) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        entry = self._data.setdefault(query, {
            "last_attempt": None,
            "last_success": None,
            "failures": 0,
        })
        entry["last_attempt"] = now
        if result_count > 0:
            entry["last_success"] = now
            entry["failures"] = 0
        else:
            entry["failures"] = entry.get("failures", 0) + 1

    # ── Prioritise queries ────────────────────────────────────────────────────

    def prioritised(self, queries: list[str]) -> list[str]:
        """
        Return queries reordered so that:
          1. Queries that have never succeeded (or failed most recently) come first
          2. Queries that succeeded last run come last
        Within each group, most-failed queries sort first.
        """
        def sort_key(q: str):
            entry = self._data.get(q, {})
            failures = entry.get("failures", 0)
            last_success = entry.get("last_success")
            # 0 = retry first, 1 = run in middle, 2 = run last
            if failures > 0:
                return (0, -failures)   # most failures → absolute first
            if not last_success:
                return (1, 0)           # never attempted → middle
            return (2, 0)               # succeeded last run → last

        reordered = sorted(queries, key=sort_key)

        # Log summary
        failed = [q for q in queries if self._data.get(q, {}).get("failures", 0) > 0]
        never  = [q for q in queries if not self._data.get(q, {}).get("last_success")]
        if failed or never:
            logger.info(
                f"search_state: prioritising {len(failed)} failed + "
                f"{len(never)} never-succeeded queries"
            )

        return reordered

    # ── Stats ─────────────────────────────────────────────────────────────────

    def summary(self) -> str:
        total    = len(self._data)
        failed   = sum(1 for v in self._data.values() if v.get("failures", 0) > 0)
        never    = sum(1 for v in self._data.values() if not v.get("last_success"))
        return f"{total} tracked, {failed} currently failing, {never} never succeeded"
