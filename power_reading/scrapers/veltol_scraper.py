from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from power_reading.scrapers.fusionsolar_scraper import PowerSnapshot


POWER_PATTERNS = {
    "grid": re.compile(r"(?is)([+-]?[0-9]+(?:[.,][0-9]+)?)\s*(W|kW|MW)\s*Grid\b"),
    "solar": re.compile(r"(?is)([+-]?[0-9]+(?:[.,][0-9]+)?)\s*(W|kW|MW)\s*Solar\b"),
    "other": re.compile(r"(?is)([+-]?[0-9]+(?:[.,][0-9]+)?)\s*(W|kW|MW)\s*Other\b"),
}


class VeltolScraper:
    def __init__(
        self,
        target_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        user_data_dir: str = ".playwright_profile_veltol",
        browser_timeout_ms: int = 45_000,
        headless: bool = False,
    ) -> None:
        self.target_url = target_url
        self.username = username
        self.password = password
        self.user_data_dir = Path(user_data_dir)
        self.browser_timeout_ms = browser_timeout_ms
        self.headless = headless

    def scrape_once(self) -> PowerSnapshot:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1600, "height": 1200},
            )
            try:
                page = context.new_page()
                page.set_default_timeout(self.browser_timeout_ms)
                page.goto(self.target_url, wait_until="domcontentloaded")
                self._accept_cookies(page)
                self._maybe_login(page)
                if page.url.rstrip("/") != self.target_url.rstrip("/"):
                    page.goto(self.target_url, wait_until="domcontentloaded")
                self._accept_cookies(page)
                self._wait_for_dashboard(page)
                text = self._collect_text(page)

                pv_kw = self._extract_kw(text, "solar")
                grid_kw = self._extract_kw(text, "grid")
                other_kw = self._extract_kw(text, "other")

                load_kw = abs(other_kw) if other_kw is not None else None

                return PowerSnapshot(
                    pv_kw=pv_kw,
                    load_kw=load_kw,
                    grid_kw=grid_kw,
                    timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                    source="veltol-text",
                    raw_excerpt=_compact_excerpt(text),
                )
            finally:
                context.close()
                browser.close()

    def read_interval_energy(self, *, start: datetime, end: datetime) -> float:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context(ignore_https_errors=True)
            try:
                page = context.new_page()
                page.set_default_timeout(self.browser_timeout_ms)
                responses: list[dict] = []

                def capture(response) -> None:
                    if "charts/balance" not in response.url or "interval=quarter_hour" not in response.url:
                        return
                    try:
                        payload = response.json()
                    except Exception:
                        return
                    if isinstance(payload, dict):
                        responses.append(payload)

                page.on("response", capture)
                page.goto(self.target_url, wait_until="domcontentloaded")
                self._accept_cookies(page)
                self._maybe_login(page)
                if page.url.rstrip("/") != self.target_url.rstrip("/"):
                    page.goto(self.target_url, wait_until="domcontentloaded")
                self._accept_cookies(page)
                for _ in range(30):
                    if responses:
                        break
                    page.wait_for_timeout(500)
                if not responses:
                    raise RuntimeError("HNG did not return its authenticated quarter-hour series.")
                local_start = start.astimezone(ZoneInfo("Europe/Bucharest"))
                return _veltol_interval_energy_mwh(responses[-1], local_start)
            finally:
                context.close()
                browser.close()

    def _maybe_login(self, page) -> None:
        email = page.locator("input[type='email'], input[name='email']")
        password = page.locator("input[type='password'], input[name='password']")
        login_btn = page.get_by_role("button", name=re.compile(r"^\s*login\s*$", re.I))

        for _ in range(10):
            on_login = "/auth/login" in page.url.lower() or (email.count() > 0 and password.count() > 0)
            if on_login:
                break
            try:
                body_text = page.locator("body").inner_text()
            except Exception:
                body_text = ""
            if "Solar Production" in body_text and "Grid" in body_text and "Solar" in body_text:
                return
            page.wait_for_timeout(500)
        else:
            return

        if not (self.username and self.password):
            raise RuntimeError("HNG/Veltol credentials are missing.")

        page.wait_for_timeout(1_500)
        email.first.fill(self.username)
        password.first.fill(self.password)
        page.wait_for_timeout(300)

        for _ in range(2):
            if login_btn.count() > 0:
                login_btn.first.click()
            else:
                password.first.press("Enter")
            try:
                page.wait_for_url(re.compile(r".*/locations/\d+.*"), timeout=12_000)
                page.wait_for_timeout(2_000)
                return
            except PlaywrightTimeoutError:
                if "/auth/login" not in page.url.lower():
                    page.wait_for_timeout(2_000)
                    return
                page.wait_for_timeout(1_500)
                email.first.fill(self.username)
                password.first.fill(self.password)

        raise RuntimeError("HNG/Veltol login did not complete.")

    def _accept_cookies(self, page) -> None:
        buttons = (
            page.get_by_role("button", name=re.compile(r"accept all cookies", re.I)),
            page.get_by_role("button", name=re.compile(r"only necessary cookies", re.I)),
        )
        for button in buttons:
            try:
                if button.count() > 0 and button.first.is_visible():
                    button.first.click(timeout=1_000)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                continue

    def _wait_for_dashboard(self, page) -> None:
        for _ in range(20):
            text = self._collect_text(page)
            if "Solar Production" in text and "Grid" in text and "Solar" in text:
                return
            page.wait_for_timeout(1_000)
        raise RuntimeError("HNG/Veltol dashboard did not finish loading.")

    def _collect_text(self, page) -> str:
        parts: list[str] = []
        try:
            body = page.locator("body").inner_text()
            if body:
                parts.append(body)
        except Exception:
            pass
        try:
            svg_text = page.evaluate(
                """() => Array.from(document.querySelectorAll('svg text, svg tspan'))
                .map((el) => (el.textContent || '').trim())
                .filter(Boolean)
                .join('\\n')"""
            )
            if svg_text:
                parts.append(svg_text)
        except Exception:
            pass
        return "\n".join(parts)

    def _extract_kw(self, text: str, label: str) -> Optional[float]:
        pattern = POWER_PATTERNS[label]
        match = pattern.search(text)
        if not match:
            return None
        value = _parse_number(match.group(1))
        unit = (match.group(2) or "kW").lower()
        if value is None:
            return None
        if unit == "mw":
            return value * 1000.0
        if unit == "w":
            return value / 1000.0
        return value


def _parse_number(raw: str) -> Optional[float]:
    cleaned = raw.strip().replace("\u00a0", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _compact_excerpt(text: str, max_len: int = 1600) -> str:
    one_line = re.sub(r"\s+", " ", text).strip()
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 3] + "..."


def _veltol_interval_energy_mwh(payload: dict, interval_start: datetime) -> float:
    data = payload.get("data", {})
    matches = []
    for item in data.get("measurements", []):
        try:
            timestamp = datetime.fromisoformat(str(item["timeIso"]))
        except (KeyError, TypeError, ValueError):
            continue
        if timestamp == interval_start:
            matches.append(item)
    if len(matches) != 1:
        raise RuntimeError(
            f"HNG history expected one quarter at {interval_start.isoformat()}, found {len(matches)}."
        )
    try:
        production = float(matches[0]["production"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("HNG quarter production is missing or invalid.") from exc
    unit = str(matches[0].get("unit") or data.get("unit") or "").upper()
    divisors = {"W": 1_000_000.0, "KW": 1000.0, "MW": 1.0}
    if unit not in divisors or production < 0:
        raise RuntimeError(f"HNG returned an invalid quarter power unit/value: {unit!r}.")
    return production / divisors[unit] * 0.25

