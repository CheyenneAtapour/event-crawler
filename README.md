# San Diego Event Crawler

Crawls events from venues, aggregators, Facebook, Instagram, and the open web across San Diego. Displays them on a calendar website — click a day to see all events.

## Setup

```bash
./setup.sh
```

This creates a Python virtualenv, installs dependencies, and downloads the Playwright browser.

## Start the server

```bash
cd backend && source .venv/bin/activate && uvicorn main:app --reload
```

Open **http://localhost:8000**

## Running the crawler

### Master script — does everything in order

```bash
# Full run: discover new sources, then scrape all events
python crawl.py

# Just scrape events from known sources (use when searches are throttled)
python crawl.py --events

# Connect NordVPN before searching (bypasses DDG/Bing IP blocks)
python crawl.py --vpn

# Just grow sources.txt, skip event scraping
python crawl.py --sources
```

`crawl.py` runs three steps in sequence:
1. **Grow sources** — searches for new SD venue/event URLs → appends to `sources.txt`
2. **Venue discovery** — searches by category (bars, comedy clubs, etc.) → appends to `sources.txt`
3. **Scrape events** — pulls events from all sources → saves to `events.db`

If searches get throttled (DDG/Bing block us), steps 1 and 2 add nothing and step 3 still runs against all already-known sources. Sources accumulate across runs over time.

### Individual commands

```bash
# Scrape events only (via API):
curl -X POST http://localhost:8000/api/scrape

# Grow sources via web search (via API):
curl -X POST http://localhost:8000/api/grow-sources
curl -X POST "http://localhost:8000/api/grow-sources?depth=2"   # follow links one level deeper

# Discover venues by category (via API):
curl -X POST http://localhost:8000/api/grow-sources/venues
```

Scrapers run automatically every night at 2 AM. Source discovery runs every Sunday at 3 AM.

## Search API keys (recommended)

DDG and Bing both block automated searches after a few queries. For reliable source discovery, add at least one API key.

```bash
cp .env.example .env
# then edit .env
```

**Brave Search** — 2,000 free queries/month, no credit card required (recommended)
1. Sign up at https://api.search.brave.com/
2. Add `BRAVE_API_KEY=your_key` to `.env`

**Google Custom Search** — 100 free queries/day
1. Create a search engine at https://programmablesearchengine.google.com/ (set to "Search the entire web")
2. Get an API key from https://console.cloud.google.com (enable Custom Search API)
3. Add `GOOGLE_API_KEY` and `GOOGLE_CSE_ID` to `.env`

Without any keys the crawler falls back to DDG via Playwright (slower but works).

## Sources

All event sources are listed in `sources.txt` — one per line, format `type | name | url`. Edit this file to add or remove sources manually. The grow scripts append new discoveries automatically.

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/events?date=YYYY-MM-DD` | Events for a specific day |
| `GET` | `/api/events?month=YYYY-MM` | Events for a month |
| `GET` | `/api/events/dates?month=YYYY-MM` | Dates with event counts (calendar dots) |
| `GET` | `/api/sources` | All scraped sources with event counts |
| `POST` | `/api/scrape` | Trigger all scrapers |
| `POST` | `/api/grow-sources` | Grow sources list via web search |
| `POST` | `/api/grow-sources/venues` | Discover venues by category |
| `GET` | `/api/docs` | Interactive API docs (Swagger) |

## Project structure

```
crawl.py                 Master script — run everything (sources + events)
backend/
├── main.py              FastAPI app + API routes
├── db.py                SQLite + scraped_sources tracking table
├── scheduler.py         Nightly scrape (2 AM) + weekly source growth (Sun 3 AM)
├── scrapers/
│   ├── __init__.py      run_all() — runs all scrapers in sequence
│   ├── http.py          PoliteCrawler — per-domain rate limiting + retry/backoff
│   ├── nineteen_hz.py   19hz.info (SoCal electronic music)
│   ├── meetup.py        Meetup San Diego (GraphQL + Playwright fallback)
│   ├── events_com.py    events.com
│   ├── umbrella.py      Afternoon Umbrella Friends
│   ├── venues.py        11 hardcoded SD venue websites
│   ├── social.py        33 Facebook pages + 16 Instagram accounts (Playwright)
│   ├── dynamic_venues.py  All venues from sources.txt + finds their social links
│   ├── grow_sources.py  Web search (Google/Brave/DDG) → grow sources.txt
│   ├── gmaps.py         Venue category discovery → grow sources.txt
│   └── discover.py      Link-crawl discovery → discovered_sources.json
docs/
├── index.html           Calendar UI (FullCalendar v6)
├── style.css
└── app.js
sources.txt              All known event sources — edit to add, auto-grown by crawlers
events.db                SQLite database (created on first run)
```
