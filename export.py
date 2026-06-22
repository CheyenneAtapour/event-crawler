#!/usr/bin/env python3
"""
Export events.db → frontend/data/YYYY-MM.json (one file per month).
The frontend reads these static files directly, so no API server is needed.

Run standalone:
    python export.py

Or called automatically by crawl.py after scraping.
"""
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))

DATA_DIR = Path(__file__).parent / "frontend" / "data"


def export_to_json() -> int:
    from db import get_conn

    DATA_DIR.mkdir(exist_ok=True)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY date, start_time NULLS LAST"
        ).fetchall()

    by_month: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        event = dict(row)
        month = event["date"][:7]  # YYYY-MM
        by_month[month].append(event)

    for month, events in sorted(by_month.items()):
        path = DATA_DIR / f"{month}.json"
        path.write_text(json.dumps(events))
        print(f"  wrote {path.name}  ({len(events)} events)")

    # Index file — list of months with event counts, for the frontend to know
    # which month files exist without trial-and-error fetching.
    index = [
        {"month": m, "count": len(e)}
        for m, e in sorted(by_month.items())
    ]
    (DATA_DIR / "index.json").write_text(json.dumps(index))

    total = sum(len(e) for e in by_month.values())
    print(f"\n  {total} events across {len(by_month)} months exported to {DATA_DIR}")
    return total


def push_to_github(commit_msg: str = "") -> bool:
    """Stage frontend/data/, commit, and push to origin."""
    repo = Path(__file__).parent

    if not commit_msg:
        from datetime import datetime
        commit_msg = f"data: update events {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    steps = [
        (["git", "add", "frontend/data/"], "staging data files"),
        (["git", "commit", "-m", commit_msg], "committing"),
        (["git", "push"], "pushing to GitHub"),
    ]

    for cmd, label in steps:
        result = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
        if result.returncode != 0:
            # "nothing to commit" is not an error
            if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
                print(f"  git: nothing to commit — skipping push")
                return True
            print(f"  git {label} failed: {result.stderr.strip() or result.stdout.strip()}")
            return False
        print(f"  git {label} OK")

    return True


if __name__ == "__main__":
    export_to_json()
    push_to_github()
