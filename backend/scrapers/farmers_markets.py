"""
San Diego Farmers Markets — weekly recurring events.

Since markets run on a fixed day/time every week, we generate occurrences
for the next WEEKS_AHEAD weeks rather than scraping a website.

Add new markets to MARKETS below. Fields:
  name        display name
  day         0=Mon 1=Tue 2=Wed 3=Thu 4=Fri 5=Sat 6=Sun
  start_time  HH:MM (24h)
  end_time    HH:MM (24h)
  venue       location name
  address     street address
  url         market website
  notes       shown as description
"""
from __future__ import annotations

from datetime import date, timedelta

from .base import BaseScraper

WEEKS_AHEAD = 16   # how many weeks of occurrences to generate

MARKETS = [
    # ── Sunday ───────────────────────────────────────────────────────────────
    dict(
        name="La Jolla Open Aire Market",
        day=6, start_time="09:00", end_time="13:00",
        venue="La Jolla Elementary School",
        address="7335 Girard Ave, La Jolla",
        url="https://lajollamarket.com/",
        notes="Year-round certified farmers market in the heart of La Jolla village.",
    ),
    dict(
        name="Hillcrest Farmers Market",
        day=6, start_time="09:00", end_time="14:00",
        venue="DMV Parking Lot",
        address="3960 Normal St, Hillcrest",
        url="https://www.hillcrestfarmersmarket.com/",
        notes="One of San Diego's largest markets. Local produce, food vendors, and live music.",
    ),
    dict(
        name="Leucadia Farmers Market",
        day=6, start_time="10:00", end_time="14:00",
        venue="Paul Ecke Central School",
        address="185 Union St, Encinitas",
        url="https://www.leucadiafarmersmarket.com/",
        notes="Certified farmers market in Encinitas/Leucadia.",
    ),
    dict(
        name="Mira Mesa Farmers Market",
        day=6, start_time="08:30", end_time="13:00",
        venue="Mira Mesa Community Park",
        address="8865 New Salem St, Mira Mesa",
        url="https://www.sdmiraboard.org/",
        notes="Sunday morning market in Mira Mesa.",
    ),
    dict(
        name="Rancho Santa Fe Farmers Market",
        day=6, start_time="09:00", end_time="13:00",
        venue="Rancho Santa Fe Village",
        address="Paseo Delicias, Rancho Santa Fe",
        url="https://www.rsfvillage.com/",
        notes="Upscale weekly market in Rancho Santa Fe village.",
    ),
    dict(
        name="Del Mar Farmers Market (Sunday)",
        day=6, start_time="13:00", end_time="17:00",
        venue="Del Mar Plaza",
        address="1555 Camino Del Mar, Del Mar",
        url="https://www.delmarfarmersmarket.com/",
        notes="Afternoon Sunday market at Del Mar Plaza.",
    ),

    # ── Saturday ─────────────────────────────────────────────────────────────
    dict(
        name="Little Italy Mercato",
        day=5, start_time="08:00", end_time="14:00",
        venue="Little Italy",
        address="Date St between Columbia & Kettner, Little Italy",
        url="https://www.littleitalysd.com/events/mercato",
        notes="San Diego's largest weekly market — 175+ vendors, local produce, artisans, and food.",
    ),
    dict(
        name="Del Mar Farmers Market (Saturday)",
        day=5, start_time="13:00", end_time="17:00",
        venue="Del Mar Plaza",
        address="1555 Camino Del Mar, Del Mar",
        url="https://www.delmarfarmersmarket.com/",
        notes="Afternoon Saturday market at Del Mar Plaza.",
    ),
    dict(
        name="Vista Farmers Market",
        day=5, start_time="08:00", end_time="12:00",
        venue="Santa Fe Drive",
        address="325 Santa Fe Dr, Vista",
        url="https://www.vistafarmersmarket.com/",
        notes="Saturday morning market in downtown Vista.",
    ),
    dict(
        name="Spring Valley Farmers Market",
        day=5, start_time="08:00", end_time="12:00",
        venue="Spring Valley",
        address="Spring Valley Town Center, Spring Valley",
        url="",
        notes="Saturday morning market in Spring Valley.",
    ),

    # ── Wednesday ────────────────────────────────────────────────────────────
    dict(
        name="Ocean Beach Farmers Market",
        day=2, start_time="16:00", end_time="20:00",
        venue="Newport Ave & Bacon St",
        address="4900 Newport Ave, Ocean Beach",
        url="https://www.oceanbeachsandiego.com/",
        notes="Wednesday evening street market in OB. Local produce, food, arts & crafts.",
    ),
    dict(
        name="Pacific Beach Farmers Market",
        day=2, start_time="08:00", end_time="12:00",
        venue="Promenade at Pacifica",
        address="1180 Garnet Ave, Pacific Beach",
        url="https://www.pbfarmersmarket.com/",
        notes="Wednesday morning certified farmers market in Pacific Beach.",
    ),

    # ── Thursday ─────────────────────────────────────────────────────────────
    dict(
        name="North Park Farmers Market",
        day=3, start_time="15:00", end_time="19:30",
        venue="University Ave & 30th St",
        address="2900 University Ave, North Park",
        url="https://www.northparkfarmersmarket.com/",
        notes="Thursday afternoon market in the heart of North Park.",
    ),
    dict(
        name="Chula Vista Farmers Market",
        day=3, start_time="15:00", end_time="19:00",
        venue="Third Ave Village",
        address="300 Third Ave, Chula Vista",
        url="https://www.thirdavefarmersmarket.com/",
        notes="Thursday evening market in downtown Chula Vista.",
    ),

    # ── Tuesday ──────────────────────────────────────────────────────────────
    dict(
        name="Santee Farmers Market",
        day=1, start_time="14:00", end_time="18:00",
        venue="Town Center Community Park East",
        address="550 Park Center Dr, Santee",
        url="",
        notes="Tuesday afternoon market in Santee.",
    ),

    # ── Friday ───────────────────────────────────────────────────────────────
    dict(
        name="National City Farmers Market",
        day=4, start_time="14:00", end_time="18:00",
        venue="Brick Row",
        address="901 National City Blvd, National City",
        url="",
        notes="Friday afternoon market in National City.",
    ),
    dict(
        name="Little Italy Amici Market",
        day=4, start_time="08:00", end_time="14:00",
        venue="Piazza della Famiglia",
        address="1735 India St, Little Italy",
        url="https://www.littleitalysd.com/",
        notes="Friday morning neighborhood market in Little Italy.",
    ),
]


class FarmersMarketsScraper(BaseScraper):
    name = "farmers-markets"

    async def scrape(self) -> list[dict]:
        today = date.today()
        events: list[dict] = []

        for market in MARKETS:
            day_of_week = market["day"]  # 0=Mon … 6=Sun

            # Find next occurrence on or after today
            days_until = (day_of_week - today.weekday()) % 7
            start = today + timedelta(days=days_until)

            for week in range(WEEKS_AHEAD):
                occ = start + timedelta(weeks=week)
                events.append(self.make_event(
                    title=market["name"],
                    description=market.get("notes"),
                    date=occ.strftime("%Y-%m-%d"),
                    start_time=market.get("start_time"),
                    end_time=market.get("end_time"),
                    venue=market.get("venue"),
                    address=market.get("address"),
                    city="San Diego",
                    url=market.get("url") or None,
                    tags="farmers-market",
                ))

        self.logger.info(
            f"farmers-markets: {len(events)} occurrences "
            f"({len(MARKETS)} markets × {WEEKS_AHEAD} weeks)"
        )
        return events
