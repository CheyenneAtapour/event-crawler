import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "events.db"


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scraped_sources (
                url         TEXT PRIMARY KEY,
                scraped_at  TEXT NOT NULL,
                event_count INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                description TEXT,
                date        TEXT NOT NULL,
                start_time  TEXT,
                end_time    TEXT,
                venue       TEXT,
                address     TEXT,
                city        TEXT DEFAULT 'San Diego',
                url         TEXT,
                source      TEXT NOT NULL,
                image_url   TEXT,
                price       TEXT,
                tags        TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(source, url)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_date   ON events(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_source ON events(source)")


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_stale_urls(urls: list[str], min_age_hours: int = 20) -> list[str]:
    """Return subset of urls not scraped within the last min_age_hours."""
    if not urls:
        return []
    with get_conn() as conn:
        placeholders = ",".join("?" * len(urls))
        rows = conn.execute(
            f"""SELECT url FROM scraped_sources
                WHERE url IN ({placeholders})
                AND scraped_at > datetime('now', '-{min_age_hours} hours')""",
            urls,
        ).fetchall()
        recently_scraped = {r["url"] for r in rows}
    return [u for u in urls if u not in recently_scraped]


def mark_scraped(url: str, event_count: int = 0) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO scraped_sources (url, scraped_at, event_count)
               VALUES (?, datetime('now'), ?)
               ON CONFLICT(url) DO UPDATE SET
                   scraped_at  = datetime('now'),
                   event_count = excluded.event_count""",
            (url, event_count),
        )


def upsert_events(events: list[dict]) -> int:
    count = 0
    with get_conn() as conn:
        for e in events:
            if not e.get("title") or not e.get("date"):
                continue
            url = e.get("url") or f"__nurl__{e['source']}__{e['date']}__{e['title'][:60]}"
            try:
                conn.execute("""
                    INSERT INTO events
                        (title, description, date, start_time, end_time,
                         venue, address, city, url, source, image_url, price, tags)
                    VALUES
                        (:title, :description, :date, :start_time, :end_time,
                         :venue, :address, :city, :url, :source, :image_url, :price, :tags)
                    ON CONFLICT(source, url) DO UPDATE SET
                        title       = excluded.title,
                        description = excluded.description,
                        start_time  = excluded.start_time,
                        end_time    = excluded.end_time,
                        venue       = excluded.venue,
                        address     = excluded.address,
                        image_url   = excluded.image_url,
                        price       = excluded.price,
                        tags        = excluded.tags,
                        updated_at  = datetime('now')
                """, {**e, "url": url})
                count += 1
            except Exception:
                pass
    return count
