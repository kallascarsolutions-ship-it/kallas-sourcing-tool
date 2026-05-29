import json
import re
import time
import logging
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = Path("debug_screenshots")
SCREENSHOTS_DIR.mkdir(exist_ok=True)


def dismiss_consent(page, site_name: str):
    consent_selectors = [
        "button[id*='accept']",
        "button[id*='consent']",
        "button[class*='accept']",
        "button[class*='consent']",
        "[data-testid='uc-accept-all-button']",
        "#usercentrics-root >> button",
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "button:has-text('Alle akzeptieren')",
        "button:has-text('Accepter alle')",
        "button:has-text('I agree')",
        "button:has-text('OK')",
        ".sp_choice_type_11",
        "#onetrust-accept-btn-handler",
        "button:has-text('Tout accepter')",
        "button:has-text('Agree')",
    ]
    for selector in consent_selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=2000):
                btn.click(timeout=3000)
                logger.info(f"{site_name}: dismissed consent banner")
                time.sleep(1)
                return
        except Exception:
            continue


def get_page(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-GB",
        viewport={"width": 1440, "height": 900},
        extra_http_headers={
            "Accept-Language": "en-GB,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }
    )
    page = context.new_page()
    return browser, page


def parse_price(text: str) -> float | None:
    text = re.sub(r"[€$£\s\xa0]", "", text)
    text = re.sub(r"[^\d.,]", "", text)
    if not text:
        return None
    if text.count(".") > 1:
        text = text.replace(".", "")
    elif text.count(",") > 1:
        text = text.replace(",", "")
    elif "." in text and "," in text:
        if text.index(",") < text.index("."):
            text = text.replace(",", "")
        else:
            text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        val = float(text)
        return val if val > 10000 else None
    except ValueError:
        return None


def fetch_autoscout24(page, car: dict) -> list[dict]:
    url = car["autoscout24_url"]
    keyword = car["search_query"].lower()
    listings = []
    car_name = car["name"]

    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(3)
        dismiss_consent(page, "AutoScout24")
        time.sleep(2)

        page.screenshot(path=str(SCREENSHOTS_DIR / f"as24_{car_name.replace(' ', '_')}.png"))

        next_data_raw = page.evaluate("""
            () => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            }
        """)

        if next_data_raw:
            try:
                data = json.loads(next_data_raw)
                page_props = data.get("props", {}).get("pageProps", {})

                # Log top-level keys to help debug structure changes
                logger.info(f"AutoScout24 __NEXT_DATA__ pageProps keys: {list(page_props.keys())}")

                # Try multiple known paths for listings
                raw = (
                    page_props.get("listings")
                    or page_props.get("searchResults", {}).get("listings")
                    or page_props.get("data", {}).get("listings")
                    or []
                )
                logger.info(f"AutoScout24 __NEXT_DATA__: {len(raw)} raw items for {car_name}")

                for item in raw:
                    try:
                        vehicle = item.get("vehicle", {})
                        make = vehicle.get("make", "")
                        model = vehicle.get("model", "")
                        version = vehicle.get("modelVersionInput", "")
                        title = " ".join(filter(None, [make, model, version])).strip()

                        keyword_parts = keyword.split()
                        if not all(k in title.lower() for k in keyword_parts[-2:]):
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
                        lid = item.get("id", "")
                        listing_url = f"https://www.autoscout24.com/offers/{lid}" if lid else url

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
                    except Exception:
                        continue
            except json.JSONDecodeError:
                logger.warning(f"AutoScout24: could not parse __NEXT_DATA__ for {car_name}")

        if not listings:
            try:
                page.wait_for_selector("article, .cldt-summary-full-item, [data-testid='result-item']", timeout=8000)
                cards = page.query_selector_all("article[data-testid='result-item'], .cldt-summary-full-item, article.listing-item")
                logger.info(f"AutoScout24 fallback: {len(cards)} cards for {car_name}")

                for card in cards:
                    try:
                        title_el = card.query_selector("h2, .cldt-summary-makemodel, [data-testid='title']")
                        price_el = card.query_selector("[data-testid='price-label'], .cldt-price, .price-block")
                        if not title_el or not price_el:
                            continue

                        title = title_el.inner_text().strip()
                        keyword_parts = keyword.split()
                        if not all(k in title.lower() for k in keyword_parts[-2:]):
                            continue

                        price = parse_price(price_el.inner_text())
                        if not price:
                            continue

                        mileage_el = card.query_selector("[data-testid='mileage']")
                        year_el = card.query_selector("[data-testid='first-registration']")
                        country_el = card.query_selector("[data-testid='location']")

                        listings.append({
                            "title": title,
                            "price_eur": price,
                            "mileage_km": mileage_el.inner_text().strip() if mileage_el else "N/A",
                            "year": year_el.inner_text().strip() if year_el else "N/A",
                            "country": country_el.inner_text().strip() if country_el else "EU",
                            "seller": "Dealer/Private",
                            "source": "AutoScout24",
                            "url": url,
                        })
                    except Exception:
                        continue
            except PlaywrightTimeout:
                logger.warning(f"AutoScout24: no cards found for {car_name}")

    except PlaywrightTimeout:
        logger.warning(f"AutoScout24: timeout for {car_name}")
        try:
            page.screenshot(path=str(SCREENSHOTS_DIR / f"as24_timeout_{car_name.replace(' ', '_')}.png"))
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"AutoScout24: error for {car_name}: {e}")

    logger.info(f"AutoScout24: {len(listings)} listings for {car_name}")
    return listings


def fetch_carandclassic(page, car: dict) -> list[dict]:
    query = car.get("carandclassic_query", car["name"])
    car_name = car["name"]
    keyword = car.get("search_query", car["name"]).lower()
    listings = []

    try:
        page.goto("https://www.carandclassic.com/search/", timeout=30000, wait_until="domcontentloaded")
        time.sleep(3)
        dismiss_consent(page, "Car and Classic")
        time.sleep(1)

        # Screenshot before search so we can see the starting state
        page.screenshot(path=str(SCREENSHOTS_DIR / f"cac_pre_{car_name.replace(' ', '_')}.png"))

        search_input = page.query_selector(
            "input[placeholder*='dream classic' i], "
            "input[name='q'], "
            "input[type='search'], "
            "input[placeholder*='search' i]"
        )
        if not search_input:
            logger.warning(f"Car and Classic: search input not found for {car_name}")
            return listings

        search_input.click()
        search_input.fill(query)
        time.sleep(0.5)

        # Try pressing Enter, then wait for navigation
        search_input.press("Enter")
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeout:
            pass
        time.sleep(2)

        logger.info(f"Car and Classic: post-search URL: {page.url}")
        page.screenshot(path=str(SCREENSHOTS_DIR / f"cac_{car_name.replace(' ', '_')}.png"))

        # Try multiple selector strategies for results
        result_selectors = [
            "a[href*='/classic-cars/'][class*='card' i]",
            "a[href*='/listing/']",
            "[class*='SearchResult']",
            "[class*='search-result']",
            "[class*='ListingCard']",
            "[class*='listing-card']",
            "article",
        ]

        cards = []
        for sel in result_selectors:
            try:
                page.wait_for_selector(sel, timeout=3000)
                cards = page.query_selector_all(sel)
                if cards:
                    logger.info(f"Car and Classic: matched selector '{sel}' — {len(cards)} elements")
                    break
            except PlaywrightTimeout:
                continue

        if not cards:
            logger.warning(f"Car and Classic: no cards after search for {car_name}")
            return listings

        for card in cards:
            try:
                title_el = card.query_selector("h2, h3, [class*='title' i], [class*='name' i], strong")
                price_el = card.query_selector("[class*='price' i], [data-testid*='price'], strong")
                if not title_el or not price_el:
                    # Try inner text of the whole card as fallback
                    card_text = card.inner_text()
                    if not any(k in card_text.lower() for k in keyword.split()[-2:]):
                        continue
                    price = parse_price(card_text)
                    if not price:
                        continue
                    title = card_text.split("\n")[0].strip()
                else:
                    title = title_el.inner_text().strip()
                    keyword_parts = keyword.split()
                    if not all(k in title.lower() for k in keyword_parts[-2:]):
                        continue
                    price = parse_price(price_el.inner_text())
                    if not price:
                        continue

                year_el = card.query_selector("[class*='year' i]")
                mileage_el = card.query_selector("[class*='mileage' i], [class*='odometer' i]")
                location_el = card.query_selector("[class*='location' i], [class*='country' i]")
                link_el = card.query_selector("a[href]") or (card if card.get_attribute("href") else None)

                listing_url = page.url
                if link_el:
                    href = link_el.get_attribute("href")
                    if href:
                        listing_url = f"https://www.carandclassic.com{href}" if href.startswith("/") else href

                listings.append({
                    "title": title,
                    "price_eur": price,
                    "mileage_km": mileage_el.inner_text().strip() if mileage_el else "N/A",
                    "year": year_el.inner_text().strip() if year_el else "N/A",
                    "country": location_el.inner_text().strip() if location_el else "EU",
                    "seller": "Dealer/Private",
                    "source": "Car and Classic",
                    "url": listing_url,
                })
            except Exception:
                continue

    except PlaywrightTimeout:
        logger.warning(f"Car and Classic: timeout for {car_name}")
        try:
            page.screenshot(path=str(SCREENSHOTS_DIR / f"cac_timeout_{car_name.replace(' ', '_')}.png"))
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Car and Classic: error for {car_name}: {e}")

    logger.info(f"Car and Classic: {len(listings)} listings for {car_name}")
    return listings


def scan_car(playwright, car: dict) -> list[dict]:
    baseline = car["market_baseline_eur"]
    threshold = car.get("alert_threshold_pct", 15)
    cutoff = baseline * (1 - threshold / 100)

    logger.info(f"Scanning: {car['name']} | Baseline: €{baseline:,.0f} | Alert below: €{cutoff:,.0f}")

    browser, page = get_page(playwright)
    all_listings = []

    try:
        all_listings += fetch_autoscout24(page, car)
        time.sleep(2)
        all_listings += fetch_carandclassic(page, car)
    finally:
        browser.close()

    seen = set()
    unique = []
    for l in all_listings:
        key = (l["title"][:25].lower(), int(l["price_eur"] / 5000))
        if key not in seen:
            seen.add(key)
            unique.append(l)

    for listing in unique:
        price = listing["price_eur"]
        if price <= 0:
            continue
        listing["car_name"] = car["name"]
        listing["market_baseline_eur"] = baseline
        diff = round((baseline - price) / baseline * 100, 1)
        listing["vs_market_pct"] = diff

    unique = [l for l in unique if l.get("vs_market_pct") is not None]
    unique.sort(key=lambda x: x["price_eur"])
    logger.info(f"{car['name']}: {len(unique)} listing(s) found")
    return unique


def run_full_scan(watchlist_path: str = "watchlist.json") -> dict:
    with open(watchlist_path) as f:
        config = json.load(f)

    results = {}
    with sync_playwright() as playwright:
        for car in config["cars"]:
            results[car["name"]] = scan_car(playwright, car)
            time.sleep(3)

    return results
