"""
One-time interactive login for Facebook and Instagram.
Opens a real visible browser window — log in normally, then close the tab.
Session cookies are saved to backend/sessions/ and reused by the scrapers.

Usage:
    python login.py facebook
    python login.py instagram
    python login.py          # both
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

SESSIONS_DIR = Path(__file__).parent / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

SITES = {
    "facebook": {
        "url":      "https://www.facebook.com/login",
        "done_url": "facebook.com",          # any FB page after login = done
        "wait_for": "a[aria-label='Home'], [data-testid='comet-root']",
        "file":     SESSIONS_DIR / "facebook.json",
    },
    "instagram": {
        "url":      "https://www.instagram.com/accounts/login/",
        "done_url": "instagram.com",
        "wait_for": "nav, [role='main']",
        "file":     SESSIONS_DIR / "instagram.json",
    },
}


async def login(site: str) -> None:
    cfg = SITES[site]
    print(f"\n{'─'*55}")
    print(f"  Logging into {site.title()}")
    print(f"  A browser window will open — log in normally.")
    print(f"  The script saves your session once you're logged in.")
    print(f"{'─'*55}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()
        await page.goto(cfg["url"])

        print(f"  Waiting for you to log in to {site.title()}…")
        print(f"  (The script will continue automatically once you're in)\n")

        # Wait until the user is logged in — poll for a post-login element
        while True:
            await asyncio.sleep(2)
            try:
                current = page.url
                if cfg["done_url"] in current:
                    # Try to find a logged-in indicator
                    logged_in = await page.query_selector(cfg["wait_for"])
                    if logged_in:
                        break
            except Exception:
                pass
            # Also stop if the user manually navigates away from the login page
            if cfg["done_url"] in page.url and "login" not in page.url:
                await asyncio.sleep(2)  # give the page a moment to settle
                break

        await ctx.storage_state(path=str(cfg["file"]))
        print(f"\n  ✅ Session saved to {cfg['file'].name}")
        await browser.close()


async def main(sites: list[str]) -> None:
    for site in sites:
        if site not in SITES:
            print(f"Unknown site: {site}. Choose: {list(SITES.keys())}")
            continue
        await login(site)
    print("\nDone — scrapers will use these sessions automatically.\n")


if __name__ == "__main__":
    targets = sys.argv[1:] or list(SITES.keys())
    asyncio.run(main(targets))
