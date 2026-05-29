import json
import re
import time
import logging
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import stealth_sync

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
        "#mde-consent-accept-btn",
        "button:has-text('Alle Cookies akzeptieren')",
        "button:has-text('Akzeptieren')",
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


def _browser_context(playwright):
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
    return browser, context


def get_page(playwright):
    browser, context = _browser_context(playwright)
    page = context.new_page()
    return browser, page


def get_stealth_page(playwright):
    """Stealth page with fingerprint patching — used for Cloudflare-protected sites."""
    browser, context = _browser_context(playwright)
    page = context.new_page()
    stealth_sync(page)
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
                raw = (
                    data.get("props", {})
                        .get("pageProps", {})
                        .get("listings", [])
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


def fetch_mobile_de(page, car: dict) -> list[dict]:
    query = car.get("mobile_de_query", car["name"]).replace(" ", "+")
    url = f"https://suchen.mobile.de/fahrzeuge/search.html?lang=en&isSearchRequest=true&s=Car&vc=Car&sortOption=PRICE_ASC&q={query}"
    car_name = car["name"]
    keyword = car.get("search_query", car["name"]).lower()
    listings = []

    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(3)
        dismiss_consent(page, "mobile.de")
        time.sleep(2)

        page.screenshot(path=str(SCREENSHOTS_DIR / f"mde_{car_name.replace(' ', '_')}.png"))

        try:
            page.wait_for_selector(
                ".cBox-body, [data-item-name='result-item'], .result-list-item",
                timeout=8000
            )
        except PlaywrightTimeout:
            logger.warning(f"mobile.de: no result containers for {car_name}")
            return listings

        cards = page.query_selector_all(
            ".cBox-body--resultitem, "
            "[data-item-name='result-item'], "
            "article.u-margin-bottom-9"
        )
        logger.info(f"mobile.de: {len(cards)} cards for {car_name}")

        for card in cards:
            try:
                title_el = card.query_selector(
                    "h2, strong.h3, .title-module h2, "
                    ".h3.u-text-break-word, a.title-module__title-link"
                )
                price_el = card.query_selector(
                    ".price-block__price, strong.h3.u-block, "
                    "[class*='price-block'] strong, .u-text-primary"
                )
                if not title_el or not price_el:
                    continue

                title = title_el.inner_text().strip()
                keyword_parts = keyword.split()
                if not all(k in title.lower() for k in keyword_parts[-2:]):
                    continue

                price = parse_price(price_el.inner_text())
                if not price:
                    continue

                mileage_text = "N/A"
                year_text = "N/A"
                location_text = "DE"

                attr_items = card.query_selector_all(
                    ".rbt-attr-item, .attributes-data li, "
                    "[class*='attribute'] span, .u-text-muted li"
                )
                for attr in attr_items:
                    text = attr.inner_text().strip()
                    if re.search(r"\d[\d\s,.]*\s*km", text, re.IGNORECASE):
                        mileage_text = text
                    elif re.match(r"^(19|20)\d{2}$", text.strip()):
                        year_text = text.strip()

                location_el = card.query_selector(
                    ".seller-info__location, [class*='seller'] .u-text-muted, "
                    "[class*='location'], .u-text-subdued"
                )
                if location_el:
                    location_text = location_el.inner_text().strip().split("\n")[0]

                link_el = card.query_selector("a[href*='/fahrzeuge/'], a[href*='mobile.de']")
                listing_url = url
                if link_el:
                    href = link_el.get_attribute("href")
                    if href:
                        listing_url = (
                            f"https://suchen.mobile.de{href}"
                            if href.startswith("/")
                            else href
                        )

                listings.append({
                    "title": title,
                    "price_eur": price,
                    "mileage_km": mileage_text,
                    "year": year_text,
                    "country": location_text,
                    "seller": "Dealer/Private",
                    "source": "mobile.de",
                    "url": listing_url,
                })
            except Exception:
                continue

    except PlaywrightTimeout:
        logger.warning(f"mobile.de: timeout for {car_name}")
        try:
            page.screenshot(path=str(SCREENSHOTS_DIR / f"mde_timeout_{car_name.replace(' ', '_')}.png"))
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"mobile.de: error for {car_name}: {e}")

    logger.info(f"mobile.de: {len(listings)} listings for {car_name}")
    return listings


def fetch_jamesedition(page, car: dict) -> list[dict]:
    query = car.get("jamesedition_query", car["name"]).replace(" ", "+")
    url = f"https://www.jamesedition.com/cars/for-sale/?q={query}&sort=price_asc"
    car_name = car["name"]
    keyword = car.get("search_query", car["name"]).lower()
    listings = []

    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(4)
        dismiss_consent(page, "JamesEdition")
        time.sleep(2)

        page.screenshot(path=str(SCREENSHOTS_DIR / f"je_{car_name.replace(' ', '_')}.png"))

        # Try __NEXT_DATA__ first
        next_data_raw = page.evaluate("""
            () => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            }
        """)

        if next_data_raw:
            try:
                data = json.loads(next_data_raw)
                # JamesEdition stores listings in different paths depending on page type
                page_props = data.get("props", {}).get("pageProps", {})
                raw = (
                    page_props.get("listings", [])
                    or page_props.get("items", [])
                    or page_props.get("results", [])
                    or page_props.get("data", {}).get("listings", [])
                    or page_props.get("initialData", {}).get("listings", [])
                )
                logger.info(f"JamesEdition __NEXT_DATA__: {len(raw)} raw items for {car_name}")

                for item in raw:
                    try:
                        title = item.get("title", "") or item.get("name", "")
                        if not title:
                            make = item.get("make", "") or item.get("brand", "")
                            model = item.get("model", "")
                            title = f"{make} {model}".strip()

                        keyword_parts = keyword.split()
                        if not all(k in title.lower() for k in keyword_parts[-2:]):
                            continue

                        price_raw = (
                            item.get("price")
                            or item.get("price_eur")
                            or item.get("priceEur")
                            or (item.get("prices", {}) or {}).get("EUR")
                        )
                        if not price_raw:
                            continue
                        price_eur = float(str(price_raw).replace(",", "").replace(".", "").split()[0]) if isinstance(price_raw, str) else float(price_raw)
                        if price_eur < 10000:
                            continue

                        mileage = item.get("mileage", "N/A") or item.get("odometer", "N/A")
                        year = item.get("year", "N/A") or item.get("manufacture_year", "N/A")
                        location = item.get("location", {})
                        if isinstance(location, dict):
                            country = location.get("country", "EU")
                        else:
                            country = str(location) if location else "EU"
                        seller = item.get("seller", {})
                        seller_name = seller.get("name", "Dealer") if isinstance(seller, dict) else "Dealer"
                        slug = item.get("slug", "") or item.get("id", "")
                        listing_url = f"https://www.jamesedition.com/cars/{slug}" if slug else url

                        listings.append({
                            "title": title,
                            "price_eur": price_eur,
                            "mileage_km": f"{mileage:,} km" if isinstance(mileage, int) else str(mileage),
                            "year": str(year),
                            "country": country,
                            "seller": seller_name,
                            "source": "JamesEdition",
                            "url": listing_url,
                        })
                    except Exception:
                        continue
            except json.JSONDecodeError:
                logger.warning(f"JamesEdition: could not parse __NEXT_DATA__ for {car_name}")

        # Fallback: parse visible cards
        if not listings:
            try:
                page.wait_for_selector(
                    ".listing-card, [data-testid='listing-card'], article, .je-listing",
                    timeout=8000
                )
                cards = page.query_selector_all(
                    ".listing-card, [data-testid='listing-card'], "
                    "article[class*='listing'], .je-listing-card"
                )
                logger.info(f"JamesEdition fallback: {len(cards)} cards for {car_name}")

                for card in cards:
                    try:
                        title_el = card.query_selector("h2, h3, [class*='title'], [class*='name']")
                        price_el = card.query_selector("[class*='price'], [data-testid*='price']")
                        if not title_el or not price_el:
                            continue

                        title = title_el.inner_text().strip()
                        keyword_parts = keyword.split()
                        if not all(k in title.lower() for k in keyword_parts[-2:]):
                            continue

                        price = parse_price(price_el.inner_text())
                        if not price:
                            continue

                        year_el = card.query_selector("[class*='year'], [data-testid*='year']")
                        mileage_el = card.query_selector("[class*='mileage'], [class*='odometer'], [data-testid*='mileage']")
                        location_el = card.query_selector("[class*='location'], [class*='country'], [data-testid*='location']")
                        link_el = card.query_selector("a[href]")

                        listing_url = url
                        if link_el:
                            href = link_el.get_attribute("href")
                            if href:
                                listing_url = f"https://www.jamesedition.com{href}" if href.startswith("/") else href

                        listings.append({
                            "title": title,
                            "price_eur": price,
                            "mileage_km": mileage_el.inner_text().strip() if mileage_el else "N/A",
                            "year": year_el.inner_text().strip() if year_el else "N/A",
                            "country": location_el.inner_text().strip() if location_el else "EU",
                            "seller": "Dealer",
                            "source": "JamesEdition",
                            "url": listing_url,
                        })
                    except Exception:
                        continue
            except PlaywrightTimeout:
                logger.warning(f"JamesEdition: no cards found for {car_name}")

    except PlaywrightTimeout:
        logger.warning(f"JamesEdition: timeout for {car_name}")
        try:
            page.screenshot(path=str(SCREENSHOTS_DIR / f"je_timeout_{car_name.replace(' ', '_')}.png"))
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"JamesEdition: error for {car_name}: {e}")

    logger.info(f"JamesEdition: {len(listings)} listings for {car_name}")
    return listings


def fetch_classicdriver(page, car: dict) -> list[dict]:
    """Classic Driver scraper using stealth mode to bypass Cloudflare JS challenge."""
    query = car.get("classicdriver_query", car["name"]).replace(" ", "+")
    url = f"https://www.classicdriver.com/en/cars?fulltext={query}&sort_by=price&sort_order=asc"
    car_name = car["name"]
    keyword = car.get("search_query", car["name"]).lower()
    listings = []

    try:
        page.goto(url, timeout=40000, wait_until="domcontentloaded")
        # Give Cloudflare JS challenge extra time to resolve
        time.sleep(5)
        dismiss_consent(page, "Classic Driver")
        time.sleep(2)

        page.screenshot(path=str(SCREENSHOTS_DIR / f"cd_{car_name.replace(' ', '_')}.png"))

        # Check if Cloudflare challenge page is still showing
        page_text = page.inner_text("body")
        if "security verification" in page_text.lower() or "checking your browser" in page_text.lower():
            logger.warning(f"Classic Driver: Cloudflare challenge not bypassed for {car_name}")
            return listings

        page.wait_for_selector(".listing-item, article, .car-item, [class*='listing']", timeout=8000)
        cards = page.query_selector_all(
            ".listing-item, article.listing, .car-listing-item, "
            "[class*='listing-card'], [class*='result-item']"
        )
        logger.info(f"Classic Driver: {len(cards)} cards for {car_name}")

        for card in cards:
            try:
                title_el = card.query_selector("h2, h3, .listing-title, .car-title, .title")
                price_el = card.query_selector(".listing-price, .price, .car-price, [class*='price']")
                if not title_el or not price_el:
                    continue

                title = title_el.inner_text().strip()
                keyword_parts = keyword.split()
                if not all(k in title.lower() for k in keyword_parts[-2:]):
                    continue

                price = parse_price(price_el.inner_text())
                if not price:
                    continue

                year_el = card.query_selector(".year, .listing-year, [class*='year']")
                mileage_el = card.query_selector(".mileage, .listing-mileage, [class*='mileage']")
                location_el = card.query_selector(".location, .listing-location, [class*='location']")
                link_el = card.query_selector("a[href*='/en/car/']")

                listing_url = url
                if link_el:
                    href = link_el.get_attribute("href")
                    if href:
                        listing_url = f"https://www.classicdriver.com{href}" if href.startswith("/") else href

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
        logger.warning(f"Classic Driver: timeout for {car_name}")
        try:
            page.screenshot(path=str(SCREENSHOTS_DIR / f"cd_timeout_{car_name.replace(' ', '_')}.png"))
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Classic Driver: error for {car_name}: {e}")

    logger.info(f"Classic Driver: {len(listings)} listings for {car_name}")
    return listings


def fetch_carandclassic(page, car: dict) -> list[dict]:
    query = car.get("carandclassic_query", car["name"]).replace(" ", "%20")
    url = f"https://www.carandclassic.com/search/?q={query}&sort=price_asc&country=europe"
    car_name = car["name"]
    keyword = car.get("search_query", car["name"]).lower()
    listings = []

    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        time.sleep(3)
        dismiss_consent(page, "Car and Classic")
        time.sleep(2)

        page.screenshot(path=str(SCREENSHOTS_DIR / f"cac_{car_name.replace(' ', '_')}.png"))

        try:
            page.wait_for_selector(
                ".listing-card, .car-listing, article[class*='listing'], [data-testid='listing']",
                timeout=8000
            )
        except PlaywrightTimeout:
            logger.warning(f"Car and Classic: no result containers for {car_name}")
            return listings

        cards = page.query_selector_all(
            ".listing-card, .car-listing, "
            "article[class*='listing'], [data-testid='listing-card']"
        )
        logger.info(f"Car and Classic: {len(cards)} cards for {car_name}")

        for card in cards:
            try:
                title_el = card.query_selector("h2, h3, [class*='title'], [class*='name']")
                price_el = card.query_selector("[class*='price'], [data-testid*='price']")
                if not title_el or not price_el:
                    continue

                title = title_el.inner_text().strip()
                keyword_parts = keyword.split()
                if not all(k in title.lower() for k in keyword_parts[-2:]):
                    continue

                price = parse_price(price_el.inner_text())
                if not price:
                    continue

                year_el = card.query_selector("[class*='year']")
                mileage_el = card.query_selector("[class*='mileage'], [class*='odometer']")
                location_el = card.query_selector("[class*='location'], [class*='country']")
                link_el = card.query_selector("a[href]")

                listing_url = url
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

    # Regular browser for most sources
    browser, page = get_page(playwright)
    # Stealth browser for Classic Driver (Cloudflare)
    stealth_browser, stealth_page = get_stealth_page(playwright)

    all_listings = []

    try:
        all_listings += fetch_autoscout24(page, car)
        time.sleep(2)
        all_listings += fetch_mobile_de(page, car)
        time.sleep(2)
        all_listings += fetch_jamesedition(page, car)
        time.sleep(2)
        all_listings += fetch_carandclassic(page, car)
        time.sleep(2)
        all_listings += fetch_classicdriver(stealth_page, car)
    finally:
        browser.close()
        stealth_browser.close()

    # Deduplicate by title prefix + price bucket
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
