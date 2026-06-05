import json
import re
import time
import logging
from urllib.parse import quote
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
    text = re.sub(r"[€$£]", "", text)
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
    url = car["autoscout24_url"]
    keyword = car["search_query"].lower()
    listings = []

    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(2)

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
    query = car.get("classicdriver_query", car["search_query"]).replace(" ", "+")
    url = f"https://www.classicdriver.com/en/cars?fulltext={query}&sort_by=price&sort_order=asc"
    keyword = car["search_query"].lower()
    listings = []

    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)
        time.sleep(2)

        # Classic Driver renders server-side — try multiple card selectors
        cards = page.query_selector_all(
            ".listing-item, "
            ".car-listing-card, "
            "article.node--type-car, "
            ".view-content .views-row, "
            "[class*='listing-card'], "
            "[class*='car-item']"
        )

        if not cards:
            # Fallback: grab all articles on the page
            cards = page.query_selector_all("article")

        for card in cards:
            try:
                title_el = (
                    card.query_selector("h2, h3, .field--name-title, [class*='title'], [class*='name']")
                )
                price_el = (
                    card.query_selector(
                        ".field--name-field-price, "
                        "[class*='price'], "
                        ".listing-price, "
                        "span[class*='price']"
                    )
                )
                if not title_el or not price_el:
                    continue

                title = title_el.inner_text().strip()
                if not any(k in title.lower() for k in keyword.split()):
                    continue

                price = parse_price(price_el.inner_text())
                if not price:
                    continue

                year_el = card.query_selector("[class*='year'], [class*='date'], .field--name-field-year")
                mileage_el = card.query_selector("[class*='mileage'], [class*='km'], .field--name-field-mileage")
                location_el = card.query_selector("[class*='location'], [class*='country'], .field--name-field-location")
                link_el = card.query_selector("a[href*='/en/car/']")

                listing_url = link_el.get_attribute("href") if link_el else url
                if listing_url and not listing_url.startswith("http"):
                    listing_url = "https://www.classicdriver.com" + listing_url

                listings.append({
                    "title": title,
                    "price_eur": price,
                    "mileage_km": mileage_el.inner_text().strip() if mileage_el else "N/A",
                    "year": year_el.inner_text().strip() if year_el else "N/A",
                    "country": location_el.inner_text().strip() if location_el else "EU",
                    "seller": "Dealer/Private",
                    "source": "Classic Driver",
                    "url": listing_url,
                })
            except Exception:
                continue

    except PlaywrightTimeout:
        logger.warning(f"Classic Driver timed out for {car['name']}")
    except Exception as e:
        logger.warning(f"Classic Driver error for {car['name']}: {e}")

    logger.info(f"Classic Driver: {len(listings)} listings for {car['name']}")
    return listings


def fetch_mobileDE(page, car: dict) -> list[dict]:
    query = car.get("mobileDE_query", car["search_query"])
    url = (
        "https://suchen.mobile.de/fahrzeuge/search.html"
        f"?isSearchRequest=true&q={quote(query)}"
        "&s=Car&sb=price&od=up"
        "&cy=DE,FR,IT,NL,CH,BE,AT,ES"
    )
    keyword = car["search_query"].lower()
    listings = []

    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)
        time.sleep(3)

        # Dismiss cookie banner if present
        try:
            page.click("[id*='consent'] button, [class*='consent'] button, #mde-consent-accept-btn", timeout=3000)
            time.sleep(1)
        except Exception:
            pass

        articles = page.query_selector_all("article")

        for article in articles:
            try:
                title_el = article.query_selector("h2, h3")
                price_el = article.query_selector("[data-testid='vip-price-label'], [class*='price-label'], [class*='price']")
                if not title_el or not price_el:
                    continue

                title = title_el.inner_text().strip()
                if not title or not any(k in title.lower() for k in keyword.split()):
                    continue

                price = parse_price(price_el.inner_text())
                if not price:
                    continue

                # Try data-testid selectors for mileage/year, fall back to regex on article text
                mileage_el = article.query_selector("[data-testid='mileage'], [data-testid='Kilometerstand']")
                year_el = article.query_selector("[data-testid='first-registration'], [data-testid='Erstzulassung']")

                article_text = article.inner_text()
                if mileage_el:
                    mileage = mileage_el.inner_text().strip()
                else:
                    m = re.search(r'(\d[\d.,]+)\s*km', article_text, re.IGNORECASE)
                    mileage = m.group(0).strip() if m else "N/A"

                if year_el:
                    year = year_el.inner_text().strip()
                else:
                    y = re.search(r'\b(19[5-9]\d|20[0-2]\d)\b', article_text)
                    year = y.group(0) if y else "N/A"

                link_el = article.query_selector("a[href*='/fahrzeuge/'], a[href*='/auto/']")
                if link_el:
                    href = link_el.get_attribute("href") or ""
                    listing_url = href if href.startswith("http") else "https://suchen.mobile.de" + href
                else:
                    listing_url = url

                listings.append({
                    "title": title,
                    "price_eur": price,
                    "mileage_km": mileage,
                    "year": year,
                    "country": "DE",
                    "seller": "Dealer/Private",
                    "source": "Mobile.de",
                    "url": listing_url,
                })
            except Exception:
                continue

    except PlaywrightTimeout:
        logger.warning(f"Mobile.de timed out for {car['name']}")
    except Exception as e:
        logger.warning(f"Mobile.de error for {car['name']}: {e}")

    logger.info(f"Mobile.de: {len(listings)} listings for {car['name']}")
    return listings


def fetch_jamesedition(page, car: dict) -> list[dict]:
    query = car.get("jamesedition_query", car["search_query"])
    url = f"https://www.jamesedition.com/cars/?q={quote(query)}&sort=price_asc"
    keyword = car["search_query"].lower()
    listings = []

    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)
        time.sleep(2)

        # JamesEdition is a React SPA — try __NEXT_DATA__ first
        next_data_raw = page.evaluate("""
            () => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            }
        """)

        if next_data_raw:
            try:
                data = json.loads(next_data_raw)
                items = (
                    data.get("props", {})
                        .get("pageProps", {})
                        .get("listings", [])
                    or
                    data.get("props", {})
                        .get("pageProps", {})
                        .get("items", [])
                )
                for item in items:
                    try:
                        title = item.get("title") or item.get("name") or ""
                        if not any(k in title.lower() for k in keyword.split()):
                            continue

                        price_raw = (
                            item.get("price", {}).get("amount")
                            or item.get("price")
                        )
                        if not price_raw:
                            continue
                        price_eur = float(str(price_raw).replace(",", "").replace(".", ""))
                        if price_eur < 5000:
                            continue

                        slug = item.get("slug") or item.get("id") or ""
                        listing_url = f"https://www.jamesedition.com/cars/{slug}" if slug else url

                        listings.append({
                            "title": title,
                            "price_eur": price_eur,
                            "mileage_km": str(item.get("mileage", "N/A")),
                            "year": str(item.get("year", "N/A")),
                            "country": item.get("location", {}).get("country", "EU") if isinstance(item.get("location"), dict) else "EU",
                            "seller": item.get("seller", {}).get("name", "Dealer") if isinstance(item.get("seller"), dict) else "Dealer",
                            "source": "JamesEdition",
                            "url": listing_url,
                        })
                    except (KeyError, TypeError, ValueError):
                        continue
            except (json.JSONDecodeError, Exception):
                pass

        # DOM fallback if JSON gave nothing
        if not listings:
            cards = page.query_selector_all(
                "[class*='ListingCard'], "
                "[class*='listing-card'], "
                "[class*='listing-item'], "
                "article"
            )
            for card in cards:
                try:
                    title_el = card.query_selector("h2, h3, [class*='title'], [class*='name']")
                    price_el = card.query_selector("[class*='price'], [data-testid='price']")
                    if not title_el or not price_el:
                        continue

                    title = title_el.inner_text().strip()
                    if not any(k in title.lower() for k in keyword.split()):
                        continue

                    price = parse_price(price_el.inner_text())
                    if not price:
                        continue

                    link_el = card.query_selector("a[href]")
                    href = link_el.get_attribute("href") if link_el else ""
                    listing_url = href if href.startswith("http") else f"https://www.jamesedition.com{href}"

                    card_text = card.inner_text()
                    y = re.search(r'\b(19[5-9]\d|20[0-2]\d)\b', card_text)
                    m = re.search(r'(\d[\d.,]+)\s*km', card_text, re.IGNORECASE)

                    listings.append({
                        "title": title,
                        "price_eur": price,
                        "mileage_km": m.group(0).strip() if m else "N/A",
                        "year": y.group(0) if y else "N/A",
                        "country": "EU",
                        "seller": "Dealer",
                        "source": "JamesEdition",
                        "url": listing_url,
                    })
                except Exception:
                    continue

    except PlaywrightTimeout:
        logger.warning(f"JamesEdition timed out for {car['name']}")
    except Exception as e:
        logger.warning(f"JamesEdition error for {car['name']}: {e}")

    logger.info(f"JamesEdition: {len(listings)} listings for {car['name']}")
    return listings


def scan_car(playwright, car: dict) -> list[dict]:
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
        time.sleep(1)
        all_listings += fetch_mobileDE(page, car)
        time.sleep(1)
        all_listings += fetch_jamesedition(page, car)
    finally:
        browser.close()

    # Deduplicate by title prefix + price bucket
    seen = set()
    unique = []
    for l in all_listings:
        key = (l["title"][:25].lower(), int(l["price_eur"] / 5000))
        if key not in seen:
            seen.add(key)
            unique.append(l)

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
