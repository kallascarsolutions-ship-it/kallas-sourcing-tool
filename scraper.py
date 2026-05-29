import json
import re
import time
import logging
from pathlib import Path
import urllib.request
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


def _parse_as24_next_data(raw_text: str, car_name: str) -> tuple[list, int]:
    try:
        data = json.loads(raw_text)
        page_props = data.get("props", {}).get("pageProps", {})
        num_results = page_props.get("numberOfResults", 0)
        raw = (
            page_props.get("listings")
            or page_props.get("searchResults", {}).get("listings")
            or page_props.get("data", {}).get("listings")
            or []
        )
        return raw or [], num_results
    except (json.JSONDecodeError, AttributeError):
        return [], 0


def _read_next_data(page) -> str | None:
    return page.evaluate("""
        () => {
            const el = document.getElementById('__NEXT_DATA__');
            return el ? el.textContent : null;
        }
    """)


def _fetch_as24_via_requests(url: str, car_name: str) -> tuple[list, int]:
    """Fetch AutoScout24 page via plain HTTP — avoids headless browser detection."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not match:
            logger.warning(f"AutoScout24 requests: no __NEXT_DATA__ in response for {car_name}")
            return [], 0
        return _parse_as24_next_data(match.group(1), car_name)
    except Exception as e:
        logger.warning(f"AutoScout24 requests: error for {car_name}: {e}")
        return [], 0


def fetch_autoscout24(page, car: dict) -> list[dict]:
    url = car["autoscout24_url"]
    keyword = car["search_query"].lower()
    listings = []
    car_name = car["name"]

    # Try plain HTTP first — AS24 detects headless Chromium and returns empty results
    raw, num_results = _fetch_as24_via_requests(url, car_name)
    logger.info(f"AutoScout24 requests: {len(raw)} items, numberOfResults={num_results} for {car_name}")

    # Playwright fallback if requests got nothing
    if not raw:
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            time.sleep(3)
            pre_raw = _read_next_data(page)
            raw, num_results = _parse_as24_next_data(pre_raw, car_name) if pre_raw else ([], 0)
            logger.info(f"AutoScout24 playwright: {len(raw)} items, numberOfResults={num_results} for {car_name}")
            dismiss_consent(page, "AutoScout24")
            time.sleep(2)
            if not raw:
                post_raw = _read_next_data(page)
                raw, num_results = _parse_as24_next_data(post_raw, car_name) if post_raw else ([], 0)
        except Exception as e:
            logger.warning(f"AutoScout24 playwright fallback error for {car_name}: {e}")

    try:
        page.screenshot(path=str(SCREENSHOTS_DIR / f"as24_{car_name.replace(' ', '_')}.png"))
    except Exception:
        pass

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

    logger.info(f"AutoScout24: {len(listings)} listings for {car_name}")
    return listings


def fetch_carandclassic(page, car: dict) -> list[dict]:
    GBP_TO_EUR = 1.17

    query = car.get("carandclassic_query", car["name"])
    car_name = car["name"]
    keyword = car.get("search_query", car["name"]).lower()
    listings = []

    try:
        page.goto("https://www.carandclassic.com/search/", timeout=30000, wait_until="domcontentloaded")
        time.sleep(3)
        dismiss_consent(page, "Car and Classic")
        time.sleep(1)

        search_input = page.query_selector(
            "input[placeholder*='dream classic' i], "
            "input[name='q'], input[type='search'], "
            "input[placeholder*='search' i]"
        )
        if not search_input:
            logger.warning(f"Car and Classic: search input not found for {car_name}")
            page.screenshot(path=str(SCREENSHOTS_DIR / f"cac_{car_name.replace(' ', '_')}.png"))
            return listings

        search_input.click()
        search_input.fill(query)
        time.sleep(0.5)
        search_input.press("Enter")
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeout:
            pass
        time.sleep(2)

        logger.info(f"Car and Classic: post-search URL: {page.url}")
        page.screenshot(path=str(SCREENSHOTS_DIR / f"cac_{car_name.replace(' ', '_')}.png"))

        # Listing links always point to /car/CXXXXXXX or /la/CXXXXXXX
        links = page.query_selector_all("a[href*='/car/C'], a[href*='/la/C']")
        logger.info(f"Car and Classic: {len(links)} listing links for {car_name}")

        for link in links:
            try:
                href = link.get_attribute("href") or ""
                if not href:
                    continue
                listing_url = f"https://www.carandclassic.com{href}" if href.startswith("/") else href

                link_text = link.inner_text().strip()
                if not link_text:
                    continue

                # Keyword filter on card text
                keyword_parts = keyword.split()
                if not all(k in link_text.lower() for k in keyword_parts[-2:]):
                    continue

                # Extract price: first currency symbol + digits only
                price_match = re.search(r'([€£$])\s*([\d,]+(?:\.\d{2})?)', link_text)
                if not price_match:
                    continue
                currency_symbol = price_match.group(1)
                raw_price = parse_price(price_match.group())
                if not raw_price:
                    continue
                # Convert GBP to EUR
                price_eur = round(raw_price * GBP_TO_EUR) if currency_symbol == "£" else raw_price

                # Year: first 4-digit year in text
                year_match = re.search(r'\b(19|20)\d{2}\b', link_text)
                year = year_match.group() if year_match else "N/A"

                # Title: year + car name
                title = f"{year} {car_name}" if year != "N/A" else car_name

                # Mileage
                mileage_match = re.search(r'([\d,]+)\s*(miles?|km)', link_text, re.IGNORECASE)
                mileage = mileage_match.group() if mileage_match else "N/A"

                # Location: last short word/phrase before price line
                location_match = re.search(r'\b([A-Z][a-zA-Z\s]{2,20})\b(?=.*[£€])', link_text)
                location = location_match.group(1).strip() if location_match else "EU"

                listings.append({
                    "title": title,
                    "price_eur": price_eur,
                    "mileage_km": mileage,
                    "year": year,
                    "country": location,
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
