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


def dismiss_consent(page, timeout=3000):
    selectors = (
        "#onetrust-accept-btn-handler, "
        "button[data-testid='accept-all-cookies'], "
        "button[id*='accept'], "
        "button[class*='accept-all'], "
        "#mde-consent-accept-btn, "
        "[class*='consent'] button:first-child"
    )
    try:
        page.click(selectors, timeout=timeout)
        time.sleep(1)
        return True
    except Exception:
        return False


# ── Car and Classic ───────────────────────────────────────────────────────────

def fetch_carandclassic(page, car: dict) -> list[dict]:
    query = car.get("carandclassic_query", car["search_query"])
    search_url = f"https://www.carandclassic.com/search?q={quote(query)}&sort_by=price_asc"
    listings = []

    try:
        page.goto(search_url, timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(2)

        if dismiss_consent(page):
            logger.info("Car and Classic: dismissed consent banner")
            time.sleep(1)

        logger.info(f"Car and Classic: post-search URL: {page.url}")

        # Wait a bit longer for JS-rendered results
        time.sleep(3)

        # Collect unique listing links from the search results page
        all_links = page.query_selector_all("a[href]")
        all_hrefs = [l.get_attribute("href") for l in all_links if l.get_attribute("href")]

        # Debug: log sample hrefs so we can identify the correct URL pattern
        logger.info(f"Car and Classic: sample hrefs — {all_hrefs[:15]}")

        seen = set()
        listing_hrefs = []
        for href in all_hrefs:
            if any([
                "/listing/" in href,
                "/classic-cars/" in href,
                "/l/" in href,
                re.search(r"/[A-Z][0-9]{5,}", href),
            ]):
                full = href if href.startswith("http") else f"https://www.carandclassic.com{href}"
                if full not in seen:
                    seen.add(full)
                    listing_hrefs.append(full)

        logger.info(f"Car and Classic: {len(listing_hrefs)} listing links for {car['name']}")

        # Visit each listing page and extract data
        for url in listing_hrefs[:20]:
            try:
                page.goto(url, timeout=20000)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                time.sleep(0.5)

                title_el = page.query_selector("h1")
                price_el = page.query_selector(
                    "[class*='price']:not([class*='original']):not([class*='was']), "
                    "[data-testid*='price'], "
                    ".asking-price"
                )

                if not title_el or not price_el:
                    continue

                title = title_el.inner_text().strip()
                price = parse_price(price_el.inner_text())
                if not price:
                    continue

                body_text = page.inner_text("body")
                km_match = re.search(r'(\d[\d,]+)\s*km', body_text, re.IGNORECASE)
                mi_match = re.search(r'(\d[\d,]+)\s*miles', body_text, re.IGNORECASE)
                yr_match = re.search(r'\b(19[5-9]\d|20[0-2]\d)\b', title)

                mileage = (km_match or mi_match)
                location_el = page.query_selector("[class*='location'], [class*='country']")

                listings.append({
                    "title": title,
                    "price_eur": price,
                    "mileage_km": mileage.group(0).strip() if mileage else "N/A",
                    "year": yr_match.group(0) if yr_match else "N/A",
                    "country": location_el.inner_text().strip() if location_el else "UK",
                    "seller": "Dealer/Private",
                    "source": "Car and Classic",
                    "url": url,
                })
            except Exception:
                continue

    except PlaywrightTimeout:
        logger.warning(f"Car and Classic timed out for {car['name']}")
    except Exception as e:
        logger.warning(f"Car and Classic error for {car['name']}: {e}")

    logger.info(f"Car and Classic: {len(listings)} listings for {car['name']}")
    return listings


# ── AutoScout24 ───────────────────────────────────────────────────────────────

def fetch_autoscout24(page, car: dict) -> list[dict]:
    url = car["autoscout24_url"]
    keyword = car["search_query"].lower()
    listings = []

    try:
        page.goto(url, timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(2)

        if dismiss_consent(page):
            logger.info("AutoScout24: dismissed consent banner")

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
            logger.info(f"AutoScout24 __NEXT_DATA__: {len(raw_listings)} raw items for {car['name']}")

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
                        if listing_id else url
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

                    mileage_el = card.query_selector("[data-testid='mileage']")
                    year_el = card.query_selector("[data-testid='first-registration']")

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


# ── Mobile.de ─────────────────────────────────────────────────────────────────

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
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(3)

        if dismiss_consent(page):
            logger.info("Mobile.de: dismissed consent banner")
            time.sleep(1)

        articles = page.query_selector_all("article")

        for article in articles:
            try:
                title_el = article.query_selector("h2, h3")
                price_el = article.query_selector(
                    "[data-testid='vip-price-label'], "
                    "[class*='price-label'], "
                    "[class*='price']"
                )
                if not title_el or not price_el:
                    continue

                title = title_el.inner_text().strip()
                if not title or not any(k in title.lower() for k in keyword.split()):
                    continue

                price = parse_price(price_el.inner_text())
                if not price:
                    continue

                article_text = article.inner_text()
                km_match = re.search(r'(\d[\d.,]+)\s*km', article_text, re.IGNORECASE)
                yr_match = re.search(r'\b(19[5-9]\d|20[0-2]\d)\b', article_text)

                link_el = article.query_selector("a[href*='/fahrzeuge/'], a[href*='/auto/']")
                if link_el:
                    href = link_el.get_attribute("href") or ""
                    listing_url = href if href.startswith("http") else "https://suchen.mobile.de" + href
                else:
                    listing_url = url

                listings.append({
                    "title": title,
                    "price_eur": price,
                    "mileage_km": km_match.group(0).strip() if km_match else "N/A",
                    "year": yr_match.group(0) if yr_match else "N/A",
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


# ── JamesEdition ─────────────────────────────────────────────────────────────

def fetch_jamesedition(page, car: dict) -> list[dict]:
    query = car.get("jamesedition_query", car["search_query"])
    search_url = f"https://www.jamesedition.com/cars/?q={quote(query)}&sort=price_asc"
    keyword = car["search_query"].lower()
    listings = []

    try:
        page.goto(search_url, timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(2)

        # Wait for JS-rendered results
        time.sleep(3)

        # Collect unique listing links — JamesEdition URLs: /cars/{make}/{model}/{year}/{id}/
        all_links = page.query_selector_all("a[href]")
        all_hrefs = [l.get_attribute("href") for l in all_links if l.get_attribute("href")]

        # Debug: log sample hrefs so we can identify the correct URL pattern
        logger.info(f"JamesEdition: sample hrefs — {all_hrefs[:15]}")

        seen = set()
        listing_hrefs = []
        for href in all_hrefs:
            if re.match(r"^/cars/[^/]+/[^/]+/", href) or re.match(r"^/[a-z]+-for-sale/", href):
                full = f"https://www.jamesedition.com{href}"
                if full not in seen:
                    seen.add(full)
                    listing_hrefs.append(full)

        logger.info(f"JamesEdition: {len(listing_hrefs)} listing links for {car['name']}")

        # Visit each listing page — selectors confirmed from inspect element
        for url in listing_hrefs[:15]:
            try:
                page.goto(url, timeout=20000)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                time.sleep(0.5)

                title_el = page.query_selector("h1")
                # Confirmed selector from inspect: div.je2-listing-info__price > span
                price_el = page.query_selector(".je2-listing-info__price span")

                if not title_el or not price_el:
                    continue

                title = title_el.inner_text().strip()
                price = parse_price(price_el.inner_text())
                if not price:
                    continue

                body_text = page.inner_text("body")
                km_match = re.search(r'(\d[\d,]*)\s*[Kk]m', body_text)
                yr_match = re.search(r'\b(19[5-9]\d|20[0-2]\d)\b', title)
                location_el = page.query_selector(
                    "[class*='je2-listing-info__location'], "
                    "[class*='location']"
                )

                listings.append({
                    "title": title,
                    "price_eur": price,
                    "mileage_km": km_match.group(0).strip() if km_match else "N/A",
                    "year": yr_match.group(0) if yr_match else "N/A",
                    "country": location_el.inner_text().strip() if location_el else "EU",
                    "seller": "Dealer",
                    "source": "JamesEdition",
                    "url": url,
                })
            except Exception:
                continue

    except PlaywrightTimeout:
        logger.warning(f"JamesEdition timed out for {car['name']}")
    except Exception as e:
        logger.warning(f"JamesEdition error for {car['name']}: {e}")

    logger.info(f"JamesEdition: {len(listings)} listings for {car['name']}")
    return listings


# ── Scan ──────────────────────────────────────────────────────────────────────

def scan_car(playwright, car: dict) -> list[dict]:
    baseline = car["market_baseline_eur"]
    threshold = car.get("alert_threshold_pct", 15)
    cutoff = baseline * (1 - threshold / 100)

    logger.info(f"Scanning: {car['name']} | Baseline: €{baseline:,.0f} | Alert below: €{cutoff:,.0f}")

    browser, page = get_browser_page(playwright)
    all_listings = []

    try:
        all_listings += fetch_carandclassic(page, car)
        time.sleep(1)
        all_listings += fetch_autoscout24(page, car)
        time.sleep(1)
        all_listings += fetch_mobileDE(page, car)
        time.sleep(1)
        all_listings += fetch_jamesedition(page, car)
    finally:
        browser.close()

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
