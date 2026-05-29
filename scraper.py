import json
import re
import time
import logging
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def get_browser_page(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        viewport={"width": 1280, "height": 800},
    )
    page = context.new_page()
    return browser, page


def parse_price(text: str) -> float | None:
    """Extract a numeric price from a string like '€ 1,290,000' or '1.290.000'."""
    text = text.replace("\xa0", "").replace(" ", "")
    # Remove currency symbols
    text = re.sub(r"[€$£]", "", text)
    # Handle European format (1.290.000 or 1,290,000)
    numbers = re.findall(r"[\d][0-9.,]+", text)
    if not numbers:
        return None
    raw = numbers[0].replace(".", "").replace(",", "")
    try:
        val = float(raw)
        return val if val > 5000 else None
    except ValueError:
        return None


def fetch_autoscout24(page, car: dict) -> list[dict]:
    """Scrape AutoScout24 listings using Playwright."""
    url = car["autoscout24_url"]
    keyword = car["search_query"].lower()
    listings = []

    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(2)

        # Try to extract from __NEXT_DATA__
        next_data_raw = page.evaluate("""
            () => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            }
        """)

        if next_data_raw:
            data = json.loads(next_data_raw)
            raw_listings = (
                data.get("props", {})
                    .get("pageProps", {})
                    .get("listings", [])
            )

            for item in raw_listings:
                try:
                    vehicle = item.get("vehicle", {})
                    title = " ".join(filter(None, [
                        vehicle.get("make", ""),
                        vehicle.get("model", ""),
                        vehicle.get("modelVersionInput", ""),
                    ])).strip()

                    # Keyword filter
                    if not any(k in title.lower() for k in keyword.split()):
                        continue

                    price_raw = (
                        item.get("prices", {})
                            .get("public", {})
                            .get("priceRaw")
                    )
                    if not price_raw:
                        continue

                    price_eur = float(price_raw)
                    mileage = vehicle.get("mileage", "N/A")
                    year = vehicle.get("firstRegistrationYear", "N/A")
                    country = item.get("location", {}).get("countryCode", "N/A")
                    seller = item.get("seller", {}).get("name", "Unknown")
                    listing_id = item.get("id", "")
                    listing_url = (
                        f"https://www.autoscout24.com/offers/{listing_id}"
                        if listing_id else "N/A"
                    )

                    listings.append({
                        "title": title,
                        "price_eur": price_eur,
                        "mileage_km": f"{mileage:,} km" if isinstance(mileage, int) else str(mileage),
                        "year": str(year),
                        "country": country,
                        "seller": seller,
                        "source": "AutoScout24",
                        "url": listing_url,
                    })

                except (KeyError, TypeError, ValueError):
                    continue

        # Fallback: parse visible listing cards
        if not listings:
            cards = page.query_selector_all("article[data-testid='result-item'], .cldt-summary-full-item")
            for card in cards:
                try:
                    title_el = card.query_selector("h2, .cldt-summary-makemodel")
                    price_el = card.query_selector("[data-testid='price-label'], .cldt-price")
                    if not title_el or not price_el:
                        continue

                    title = title_el.inner_text().strip()
                    if not any(k in title.lower() for k in keyword.split()):
                        continue

                    price = parse_price(price_el.inner_text())
                    if not price:
                        continue

                    mileage_el = card.query_selector("[data-testid='mileage'], .cldt-mileage")
                    year_el = card.query_selector("[data-testid='first-registration'], .cldt-first-registration")

                    listings.append({
                        "title": title,
                        "price_eur": price,
                        "mileage_km": mileage_el.inner_text().strip() if mileage_el else "N/A",
                        "year": year_el.inner_text().strip() if year_el else "N/A",
                        "country": "EU",
                        "seller": "Dealer/Private",
                        "source": "AutoScout24",
                        "url": url,
                    })
                except Exception:
                    continue

    except PlaywrightTimeout:
        logger.warning(f"AutoScout24 timed out for {car['name']}")
    except Exception as e:
        logger.warning(f"AutoScout24 error for {car['name']}: {e}")

    logger.info(f"AutoScout24: {len(listings)} listings for {car['name']}")
    return listings


def fetch_classicdriver(page, car: dict) -> list[dict]:
    """Scrape Classic Driver listings using Playwright."""
    query = car["classicdriver_query"].replace(" ", "+")
    url = f"https://www.classicdriver.com/en/cars?fulltext={query}&sort_by=price&sort_order=asc"
    listings = []

    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(2)

        cards = page.query_selector_all(".listing-item, article.car-listing, .cldt-listing")
        for card in cards:
            try:
                title_el = card.query_selector("h2, h3, .listing-title, .car-title")
                price_el = card.query_selector(".listing-price, .price, .car-price")
                if not title_el or not price_el:
                    continue

                title = title_el.inner_text().strip()
                price = parse_price(price_el.inner_text())
                if not price:
                    continue

                year_el = card.query_selector(".year, .listing-year")
                mileage_el = card.query_selector(".mileage, .listing-mileage")
                location_el = card.query_selector(".location, .listing-location")

                listings.append({
                    "title": title,
                    "price_eur": price,
                    "mileage_km": mileage_el.inner_text().strip() if mileage_el else "N/A",
                    "year": year_el.inner_text().strip() if year_el else "N/A",
                    "country": location_el.inner_text().strip() if location_el else "EU",
                    "seller": "Dealer/Private",
                    "source": "Classic Driver",
                    "url": url,
                })
            except Exception:
                continue

    except PlaywrightTimeout:
        logger.warning(f"Classic Driver timed out for {car['name']}")
    except Exception as e:
        logger.warning(f"Classic Driver error for {car['name']}: {e}")

    logger.info(f"Classic Driver: {len(listings)} listings for {car['name']}")
    return listings


def scan_car(playwright, car: dict) -> list[dict]:
    """Scan all sources for a single car and return flagged deals."""
    baseline = car["market_baseline_eur"]
    threshold = car.get("alert_threshold_pct", 15)
    cutoff = baseline * (1 - threshold / 100)

    logger.info(f"Scanning: {car['name']} | Baseline: €{baseline:,.0f} | Alert below: €{cutoff:,.0f}")

    browser, page = get_browser_page(playwright)
    all_listings = []

    try:
        all_listings += fetch_autoscout24(page, car)
        time.sleep(1)
        all_listings += fetch_classicdriver(page, car)
    finally:
        browser.close()

    # Deduplicate
    seen = set()
    unique = []
    for l in all_listings:
        key = (l["title"][:25].lower(), int(l["price_eur"] / 5000))
        if key not in seen:
            seen.add(key)
            unique.append(l)

    # Flag deals
    deals = []
    for listing in unique:
        price = listing["price_eur"]
        if price <= 0:
            continue
        if price <= cutoff:
            listing["car_name"] = car["name"]
            listing["market_baseline_eur"] = baseline
            listing["discount_pct"] = round((baseline - price) / baseline * 100, 1)
            deals.append(listing)

    deals.sort(key=lambda x: x["price_eur"])
    logger.info(f"{car['name']}: {len(deals)} deal(s) flagged")
    return deals


def run_full_scan(watchlist_path: str = "watchlist.json") -> dict:
    with open(watchlist_path, "r") as f:
        config = json.load(f)

    results = {}

    with sync_playwright() as playwright:
        for car in config["cars"]:
            results[car["name"]] = scan_car(playwright, car)
            time.sleep(3)

    return results
