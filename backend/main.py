import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from db import init_db, get_conn
from scheduler import setup_scheduler, shutdown_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FRONTEND = Path(__file__).parent.parent / "docs"

app = FastAPI(title="San Diego Events", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
    setup_scheduler()


@app.on_event("shutdown")
def on_shutdown():
    shutdown_scheduler()


# ── API routes ──────────────────────────────────────────────────────────────

@app.get("/api/events")
def get_events(
    date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    month: Optional[str] = Query(None, description="YYYY-MM"),
    source: Optional[str] = None,
):
    with get_conn() as conn:
        sql = "SELECT * FROM events WHERE 1=1"
        params: list = []
        if date:
            sql += " AND date = ?"
            params.append(date)
        elif month:
            sql += " AND date LIKE ?"
            params.append(f"{month}%")
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY date, start_time NULLS LAST"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


@app.get("/api/events/dates")
def get_event_dates(month: Optional[str] = Query(None, description="YYYY-MM")):
    """Return {date, count} pairs for calendar dot rendering."""
    with get_conn() as conn:
        sql = "SELECT date, COUNT(*) as count FROM events"
        params: list = []
        if month:
            sql += " WHERE date LIKE ?"
            params.append(f"{month}%")
        sql += " GROUP BY date ORDER BY date"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


@app.get("/api/sources")
def get_sources():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT source, COUNT(*) as count FROM events GROUP BY source ORDER BY count DESC"
        ).fetchall()
        return [dict(r) for r in rows]


@app.post("/api/scrape")
async def trigger_scrape(background_tasks: BackgroundTasks):
    async def _run():
        from scrapers import run_all
        await run_all()

    background_tasks.add_task(_run)
    return {"status": "started"}


@app.post("/api/discover")
async def trigger_discover(background_tasks: BackgroundTasks):
    """
    Crawl the web to find new San Diego event sources.
    Results are written to discovered_sources.json in the project root.
    """
    async def _run():
        from scrapers.discover import run_discovery
        await run_discovery()

    background_tasks.add_task(_run)
    return {"status": "started", "output": "discovered_sources.json"}


@app.get("/api/discover/results")
def get_discover_results():
    """Return the last discovery run results."""
    import json
    from pathlib import Path
    output = Path(__file__).parent.parent / "discovered_sources.json"
    if not output.exists():
        return []
    return json.loads(output.read_text())


@app.post("/api/grow-sources")
async def trigger_grow_sources(
    background_tasks: BackgroundTasks,
    depth: int = 1,
):
    """
    Search DuckDuckGo + Google, crawl existing sources for new links,
    score candidates, and append to sources.txt.
    depth=1 (default) or depth=2 for one extra hop.
    Set GOOGLE_API_KEY + GOOGLE_CSE_ID env vars for Google results too.
    """
    async def _run():
        from scrapers.grow_sources import run_grow
        await run_grow(crawl_depth=depth)

    background_tasks.add_task(_run)
    return {"status": "started", "depth": depth, "output": "sources.txt"}


@app.post("/api/grow-sources/venues")
async def trigger_venue_discovery(background_tasks: BackgroundTasks):
    """
    Discover San Diego venues by category (music, bars, comedy, etc.)
    using Google + DuckDuckGo searches, then find each venue's events page.
    """
    async def _run():
        from scrapers.gmaps import run_gmaps_discovery
        await run_gmaps_discovery()

    background_tasks.add_task(_run)
    return {"status": "started", "output": "sources.txt"}


# ── Static frontend ──────────────────────────────────────────────────────────

if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(FRONTEND / "index.html"))
