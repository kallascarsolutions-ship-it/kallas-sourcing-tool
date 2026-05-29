import json
import logging
from scraper import run_full_scan
from emailer import send_email

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    logger.info("KCS Sourcing Tool — starting daily scan")

    with open("watchlist.json", "r") as f:
        config = json.load(f)

    results = run_full_scan("watchlist.json")

    total = sum(len(v) for v in results.values())
    logger.info(f"Scan complete — {total} deal(s) flagged across {len(results)} cars")

    send_email(results, config)
    logger.info("Done")


if __name__ == "__main__":
    main()
