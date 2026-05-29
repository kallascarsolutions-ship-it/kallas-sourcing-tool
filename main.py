import json
import csv
import logging
from pathlib import Path
from datetime import date
from scraper import run_full_scan

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)


def save_csv(results: dict) -> Path:
    today = date.today().isoformat()
    filepath = REPORTS_DIR / f"{today}.csv"

    rows = []
    for car_name, listings in results.items():
        if listings:
            for l in listings:
                vs_market = l.get("vs_market_pct", 0)
                if vs_market > 0:
                    market_status = f"{vs_market}% BELOW"
                elif vs_market < 0:
                    market_status = f"{abs(vs_market)}% ABOVE"
                else:
                    market_status = "AT MARKET"

                rows.append({
                    "Date":             today,
                    "Car":              car_name,
                    "Listing Title":    l.get("title", "N/A"),
                    "Asking Price":     f"€{l['price_eur']:,.0f}",
                    "Market Baseline":  f"€{l['market_baseline_eur']:,.0f}",
                    "vs Market":        market_status,
                    "Year":             l.get("year", "N/A"),
                    "Mileage":          l.get("mileage_km", "N/A"),
                    "Country":          l.get("country", "N/A"),
                    "Seller":           l.get("seller", "N/A"),
                    "Source":           l.get("source", "N/A"),
                    "URL":              l.get("url", "N/A"),
                })
        else:
            rows.append({
                "Date":             today,
                "Car":              car_name,
                "Listing Title":    "No listings found today",
                "Asking Price":     "",
                "Market Baseline":  "",
                "vs Market":        "",
                "Year":             "",
                "Mileage":          "",
                "Country":          "",
                "Seller":           "",
                "Source":           "",
                "URL":              "",
            })

    fieldnames = [
        "Date", "Car", "Listing Title", "Asking Price", "Market Baseline",
        "vs Market", "Year", "Mileage", "Country", "Seller", "Source", "URL"
    ]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    total = sum(len(v) for v in results.values())
    logger.info(f"CSV saved: {filepath} — {total} listing(s) across {len(results)} cars")
    return filepath


def main():
    logger.info("KCS Sourcing Tool — starting daily scan")

    results = run_full_scan("watchlist.json")

    total = sum(len(v) for v in results.values())
    logger.info(f"Scan complete — {total} listing(s) found across {len(results)} cars")

    save_csv(results)


if __name__ == "__main__":
    main()
