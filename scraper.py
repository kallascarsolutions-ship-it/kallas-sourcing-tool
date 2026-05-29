import requests
from bs4 import BeautifulSoup
import json
import re
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

EU_COUNTRIES = "D,A,B,CH,I,NL,F,E,P,S,DK,N,FIN"


def fetch_autoscout24(make: str, model: str, keyword: str) -> list[dict]:
    """Search AutoScout24 and return matching listings as dicts."""
    url = f"https://www.autoscout24.com/lst/{make}/{model}"
    params = {
        "sort": "price",
        "desc": "0",
        "cy": EU_COUNTRIES,
        "ustate": "N,U",
    }

    listings = []

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"AutoScout24 request failed for {make}/{model}: {e}")
        return listings

    soup = BeautifulSoup(resp.text, "lxml")

    # AutoScout24 embeds listing data in __NEXT_DATA__ JSON
    next_data_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not next_data_tag:
        logger.warning(f"No __NEXT_DATA__ found for {make}/{model}")
        return listings

    try:
        data = json.loads(next_data_tag.string)
        raw_listings = (
            data.get("props", {})
                .get("pageProps", {})
                .get("listings", [])
        )
    except (json.JSONDecodeError, AttributeError) as e:
        logger.warning(f"Failed to parse __NEXT_DATA__ for {make}/{model}: {e}")
        return listings

    keyword_lower = keyword.lower()

    for item in raw_listings:
        try:
            title = (
                f"{item.get('vehicle', {}).get('make', '')} "
                f"{item.get('vehicle', {}).get('model', '')} "
                f"{item.get('vehicle', {}).get('modelVersionInput', '')}"
            ).strip()

            # Filter by keyword so e.g. "GT2 RS" doesn't match a base 911
            if keyword_lower not in title.lower():
                continue

            price_raw = item.get("prices", {}).get("public", {}).get("priceRaw")
            if not price_raw:
                continue

            price_eur = float(price_raw)
            mileage = item.get("vehicle", {}).get("mileage", "N/A")
            year = item.get("vehicle", {}).get("firstRegistrationYear", "N/A")
            country = item.get("location", {}).get("countryCode", "N/A")
            seller = item.get("seller", {}).get("name", "Private / Unknown")
            listing_id = item.get("id", "")
            listing_url = f"https://www.autoscout24.com/offers/{listing_id}" if listing_id else "N/A"

            listings.append({
                "title": title,
                "price_eur": price_eur,
                "mileage_km": mileage,
                "year": year,
                "country": country,
                "seller": seller,
                "source": "AutoScout24",
                "url": listing_url,
            })

        except (KeyError, TypeError, ValueError):
            continue

    logger.info(f"AutoScout24: found {len(listings)} listings for {make}/{model} ({keyword})")
    return listings


def fetch_classicdriver(keyword: str) -> list[dict]:
    """Search Classic Driver for a given keyword."""
    url = "https://www.classicdriver.com/en/cars"
    params = {
        "fulltext": keyword,
        "sort_by": "price",
        "sort_order": "asc",
    }

    listings = []

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"Classic Driver request failed for '{keyword}': {e}")
        return listings

    soup = BeautifulSoup(resp.text, "lxml")

    for card in soup.select(".listing-item, .car-listing, article.listing"):
        try:
            title_el = card.select_one("h2, h3, .title, .car-title")
            price_el = card.select_one(".price, .listing-price")
            year_el = card.select_one(".year, .first-registration")
            mileage_el = card.select_one(".mileage, .km")
            location_el = card.select_one(".location, .country")

            if not title_el or not price_el:
                continue

            title = title_el.get_text(strip=True)
            price_text = price_el.get_text(strip=True)

            # Extract numeric price
            price_numbers = re.findall(r"[\d,\.]+", price_text.replace("\xa0", ""))
            if not price_numbers:
                continue
            price_eur = float(price_numbers[0].replace(",", "").replace(".", ""))
            if price_eur < 10000:
                continue

            listings.append({
                "title": title,
                "price_eur": price_eur,
                "mileage_km": mileage_el.get_text(strip=True) if mileage_el else "N/A",
                "year": year_el.get_text(strip=True) if year_el else "N/A",
                "country": location_el.get_text(strip=True) if location_el else "N/A",
                "seller": "Dealer / Private",
                "source": "Classic Driver",
                "url": "classicdriver.com",
            })

        except (AttributeError, ValueError):
            continue

    logger.info(f"Classic Driver: found {len(listings)} listings for '{keyword}'")
    return listings


def scan_car(car: dict) -> list[dict]:
    """Run all scrapers for a single car config and return flagged deals."""
    name = car["name"]
    baseline = car["market_baseline_eur"]
    threshold = car.get("alert_threshold_pct", 15)
    cutoff = baseline * (1 - threshold / 100)

    logger.info(f"Scanning: {name} | Baseline: €{baseline:,.0f} | Alert below: €{cutoff:,.0f}")

    all_listings = []

    # AutoScout24 (primary EU source)
    all_listings += fetch_autoscout24(
        car["autoscout24_make"],
        car["autoscout24_model"],
        car["autoscout24_keyword"],
    )

    # Classic Driver (secondary — collector/rare cars)
    all_listings += fetch_classicdriver(car["name"])

    # Deduplicate by approximate price + title
    seen = set()
    unique = []
    for l in all_listings:
        key = (l["title"][:30].lower(), int(l["price_eur"] / 1000))
        if key not in seen:
            seen.add(key)
            unique.append(l)

    # Flag deals below threshold
    deals = []
    for listing in unique:
        price = listing["price_eur"]
        if price <= 0:
            continue
        discount_pct = round((baseline - price) / baseline * 100, 1)
        if price <= cutoff:
            listing["car_name"] = name
            listing["market_baseline_eur"] = baseline
            listing["discount_pct"] = discount_pct
            deals.append(listing)

    deals.sort(key=lambda x: x["price_eur"])
    logger.info(f"{name}: {len(deals)} deal(s) flagged below threshold")
    return deals


def run_full_scan(watchlist_path: str = "watchlist.json") -> dict:
    """Run the full scan across all cars in the watchlist."""
    with open(watchlist_path, "r") as f:
        config = json.load(f)

    results = {}

    for car in config["cars"]:
        deals = scan_car(car)
        results[car["name"]] = deals
        time.sleep(2)  # Polite delay between requests

    return results
