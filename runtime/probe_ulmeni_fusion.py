"""One-off Ulmeni login probe; credentials stay in memory, never in logs."""
import getpass
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright
from power_reading.scrapers.fusionsolar_scraper import FusionSolarScraper
from power_reading.service import DEFAULT_FUSIONSOLAR_URL


def main():
    username = getpass.getpass("FusionSolar username (hidden): ")
    password = getpass.getpass("FusionSolar password (hidden): ")
    scraper = FusionSolarScraper(DEFAULT_FUSIONSOLAR_URL, username, password)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1920, "height": 1200})
            page = context.new_page()
            page.set_default_timeout(45000)
            page.goto(scraper.target_url, wait_until="domcontentloaded")
            scraper._maybe_login(page)
            page.wait_for_timeout(5000)
            rows = page.locator("table tbody tr").all_inner_texts()
            print(json.dumps({"status": "authenticated", "host": urlsplit(page.url).hostname,
                              "plant_rows": [row.replace(username, "[account]").replace(password, "[redacted]") for row in rows]}))
        finally:
            browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__}))
        raise SystemExit(1)
