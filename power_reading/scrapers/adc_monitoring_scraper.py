from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, TimeoutError as PlaywrightTimeoutError, sync_playwright

from power_reading.scrapers.fusionsolar_scraper import PowerSnapshot


class ADCMonitoringScraper:
    def __init__(
        self,
        target_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        plant_name: Optional[str] = None,
        user_data_dir: str = ".playwright_profile_anto_adc",
        browser_timeout_ms: int = 45_000,
        headless: bool = False,
    ) -> None:
        self.target_url = target_url
        self.username = username
        self.password = password
        self.plant_name = plant_name or "CEF Anto"
        self.user_data_dir = Path(user_data_dir)
        self.browser_timeout_ms = browser_timeout_ms
        self.headless = headless

    def scrape_once(self) -> PowerSnapshot:
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            context: BrowserContext = p.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir.resolve()),
                headless=self.headless,
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=1920,1080",
                ],
            )
            try:
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
                page = context.new_page()
                page.set_default_timeout(self.browser_timeout_ms)
                page.goto(self.target_url, wait_until="domcontentloaded")
                self._maybe_login(page)
                self._wait_for_dashboard(page)

                text = page.locator("body").first.inner_text(timeout=10_000)
                pv_kw = _extract_metric_kw(text, "PV POWER")
                grid_kw = _extract_metric_kw(text, "GRID EXCHANGE")
                raw_excerpt = _compact_excerpt(text)
                source = "adc-monitoring-text" if pv_kw is not None or grid_kw is not None else "adc-monitoring-unmatched"
                return PowerSnapshot(
                    pv_kw=pv_kw,
                    load_kw=None,
                    grid_kw=grid_kw,
                    timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                    source=source,
                    raw_excerpt=raw_excerpt,
                )
            finally:
                context.close()

    def _maybe_login(self, page) -> None:
        if not self.username or not self.password:
            return
        try:
            page.locator("input").first.wait_for(timeout=15_000)
        except Exception:
            pass
        if not _looks_like_login_page(page):
            return

        user_selectors = (
            "input[type='email']",
            "input[name*='email' i]",
            "input[name*='user' i]",
            "input[autocomplete='username']",
            "input[type='text']",
        )
        pass_selectors = (
            "input[type='password']",
            "input[name*='pass' i]",
            "input[autocomplete='current-password']",
        )
        self._fill_first(page, user_selectors, self.username)
        self._fill_first(page, pass_selectors, self.password)

        click_candidates = (
            page.get_by_role("button", name=re.compile(r"log\s*in|sign\s*in|login|autentificare", re.I)),
            page.locator("button[type='submit']"),
            page.locator("input[type='submit']"),
            page.locator("button").filter(has_text=re.compile(r"log\s*in|sign\s*in|login|autentificare", re.I)),
        )
        for locator in click_candidates:
            try:
                if locator.count() <= 0:
                    continue
                locator.first.click(timeout=5_000)
                break
            except Exception:
                continue

        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeoutError:
            pass

    def _fill_first(self, page, selectors: tuple[str, ...], value: str) -> None:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if locator.count() <= 0:
                    continue
                locator.first.fill(value, timeout=5_000)
                return
            except Exception:
                continue

    def _wait_for_dashboard(self, page) -> None:
        candidates = (
            re.compile(r"Fleet\s+Overview", re.I),
            re.compile(r"PV\s+POWER", re.I),
            re.compile(re.escape(self.plant_name), re.I),
        )
        for pattern in candidates:
            try:
                page.get_by_text(pattern).first.wait_for(timeout=20_000)
                return
            except Exception:
                continue
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except PlaywrightTimeoutError:
            pass


def _looks_like_login_page(page) -> bool:
    try:
        if page.locator("input[type='password']").count() > 0:
            return True
        text = page.locator("body").first.inner_text(timeout=5_000)
        return bool(re.search(r"log\s*in|sign\s*in|login|autentificare|password|parola", text, re.I))
    except Exception:
        return False


def _extract_metric_kw(text: str, label: str) -> Optional[float]:
    compact = re.sub(r"[ \t]+", " ", str(text or ""))
    label_pattern = r"\s+".join(re.escape(part) for part in label.split())
    patterns = (
        rf"(?is)\b{label_pattern}\b\s*(-?[0-9]+(?:[.,][0-9]+)?)\s*(kW|MW|W)\b",
        rf"(?is)\b{label_pattern}\b.*?(-?[0-9]+(?:[.,][0-9]+)?)\s*(kW|MW|W)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, compact)
        if not match:
            continue
        value = _parse_number(match.group(1))
        if value is None:
            continue
        return _to_kw(value, match.group(2).lower())
    return None


def _parse_number(raw: str) -> Optional[float]:
    cleaned = raw.strip().replace(" ", "")
    if "." in cleaned and "," in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_kw(value: float, unit: str) -> float:
    if unit == "mw":
        return value * 1000.0
    if unit == "w":
        return value / 1000.0
    return value


def _compact_excerpt(text: str, max_len: int = 1200) -> str:
    one_line = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 3] + "..."

