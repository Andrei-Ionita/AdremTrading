from __future__ import annotations

import math
import re
import time
import unicodedata
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
                responses: list[dict] = []

                def capture(response) -> None:
                    if "/api/v1/fleet/overview/grouped" not in response.url or not response.ok:
                        return
                    try:
                        payload = response.json()
                    except Exception:
                        return
                    if isinstance(payload, dict) and isinstance(payload.get("groups"), list):
                        responses[:] = [payload]

                page.on("response", capture)
                page.goto(self.target_url, wait_until="domcontentloaded")
                self._maybe_login(page)
                deadline = time.monotonic() + min(self.browser_timeout_ms / 1000.0, 15.0)
                plant_text = ""
                while not responses and time.monotonic() < deadline:
                    plant_text = _extract_plant_section(page.locator("body").first.inner_text(), self.plant_name)
                    page.wait_for_timeout(250)
                if responses:
                    pv_kw = _extract_live_site_power_kw(responses[-1], self.plant_name)
                    source = "adc-monitoring-live-api"
                    plant_text = f"{self.plant_name}: {pv_kw} kW"
                else:
                    pv_kw = _extract_live_card_power_kw(plant_text)
                    if pv_kw is None:
                        raise RuntimeError("ADC current plant power is unavailable; no live sample recorded.")
                    source = "adc-monitoring-card-power-output"
                return PowerSnapshot(
                    pv_kw=pv_kw,
                    load_kw=None,
                    grid_kw=None,
                    timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                    source=source,
                    raw_excerpt=_compact_excerpt(plant_text),
                )
            finally:
                context.close()

    def read_interval_energy(self, *, start: datetime, end: datetime) -> float:
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context: BrowserContext = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir.resolve()),
                headless=self.headless,
                viewport={"width": 1920, "height": 1080},
            )
            try:
                page = context.new_page()
                page.set_default_timeout(self.browser_timeout_ms)
                page.goto(self.target_url, wait_until="domcontentloaded")
                self._maybe_login(page)
                grouped = page.request.get(
                    "https://adc-monitoring.ro/api/v1/fleet/overview/grouped"
                )
                if not grouped.ok:
                    raise RuntimeError(f"ADC site lookup failed with HTTP {grouped.status}.")
                sites = [
                    site
                    for group in grouped.json().get("groups", [])
                    for site in group.get("sites", [])
                ]
                wanted = _normalized_text(self.plant_name)
                matches = [site for site in sites if _normalized_text(site.get("name")) == wanted]
                if len(matches) != 1:
                    raise RuntimeError(
                        f"ADC expected one site named {self.plant_name!r}, found {len(matches)}."
                    )
                return _adc_live_interval_estimate_mwh(matches[0], start, end)
            finally:
                context.close()

    def _maybe_login(self, page) -> None:
        if not self.username or not self.password:
            return
        try:
            page.wait_for_function("""(plant) =>
                !!document.querySelector("input[type='password']") ||
                (document.body.innerText || '').includes(plant)
            """, arg=self.plant_name, timeout=15_000)
        except PlaywrightTimeoutError:
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
            page.wait_for_function("""() =>
                !Array.from(document.querySelectorAll("input[type='password']"))
                  .some(element => element.getClientRects().length)
            """, timeout=15_000)
        except PlaywrightTimeoutError:
            raise RuntimeError("ADC login did not reach the authenticated dashboard.") from None

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

    def _wait_for_plant_card(self, page) -> str:
        deadline = time.monotonic() + (self.browser_timeout_ms / 1000.0)
        last_text = ""
        while time.monotonic() < deadline:
            last_text = page.locator("body").first.inner_text(timeout=10_000)
            plant_text = _extract_plant_section(last_text, self.plant_name)
            if plant_text and (
                _extract_metric_kw(plant_text, "POWER OUTPUT") is not None
                or _extract_metric_kw(plant_text, "15M AVG") is not None
            ):
                return plant_text
            page.wait_for_timeout(1000)
        return _extract_plant_section(last_text, self.plant_name) or ""


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


def _extract_plant_section(text: str, plant_name: str, max_lines: int = 12) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    wanted = _normalized_text(plant_name)
    if not wanted:
        return ""
    for index, line in enumerate(lines):
        candidate = _normalized_text(line)
        if candidate == wanted:
            section = []
            for item in lines[index : index + max_lines]:
                if section and re.match(r"^CEF\s", item, re.I):
                    break
                section.append(item)
            return "\n".join(section)
    return ""


def _extract_live_card_power_kw(text: str) -> Optional[float]:
    if re.search(r"COMM\s+FAILURE|OFFLINE|DISCONNECTED", text, re.I):
        raise RuntimeError("ADC reports a plant communication failure; no live sample recorded.")
    for label in ("CURRENT POWER", "POWER OUTPUT"):
        value = _extract_metric_kw(text, label)
        if value is not None and math.isfinite(value) and value >= 0:
            return value
    return None


def _extract_live_site_power_kw(payload: dict, plant_name: str) -> float:
    wanted = _normalized_text(plant_name)
    matches = [site for group in payload.get("groups", []) for site in group.get("sites", [])
               if _normalized_text(site.get("name")) == wanted]
    if len(matches) != 1:
        raise RuntimeError("ADC did not return exactly one matching plant.")
    site = matches[0]
    status = str(site.get("onlineStatus", "")).strip().lower()
    if status in {"offline", "disconnected", "comm_failure", "communication_failure"}:
        raise RuntimeError("ADC reports a plant communication failure; no live sample recorded.")
    raw = site.get("pvPowerKw")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise RuntimeError("ADC current plant power is unavailable; no live sample recorded.") from None
    if isinstance(raw, bool) or not math.isfinite(value) or value < 0:
        raise RuntimeError("ADC returned invalid current plant power.")
    return value


def _normalized_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_like = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", ascii_like).strip().casefold()


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


def _adc_interval_energy_mwh(payload: dict, start: datetime, end: datetime) -> float:
    expected_seconds = (end - start).total_seconds()
    covered_seconds = 0.0
    energy_mwh = 0.0
    source_timestamps: list[datetime] = []
    for item in payload.get("data", []):
        try:
            timestamp = datetime.fromisoformat(str(item["ts"]).replace("Z", "+00:00"))
            power_kw = float(item["pvPowerKw"])
            coverage = float(item["coverageSeconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("ADC power history contains an invalid bucket.") from exc
        source_timestamps.append(timestamp)
        if start <= timestamp < end:
            if power_kw < 0 or coverage <= 0 or coverage > 60:
                raise RuntimeError("ADC power history contains an invalid power or coverage value.")
            covered_seconds += coverage
            energy_mwh += power_kw / 1000.0 * coverage / 3600.0
    if covered_seconds < expected_seconds * 0.9:
        available = "no timestamped buckets"
        if source_timestamps:
            available = (
                f"buckets from {min(source_timestamps).isoformat()} "
                f"to {max(source_timestamps).isoformat()}"
            )
        raise RuntimeError(
            f"ADC power history covers only {covered_seconds:.0f} of {expected_seconds:.0f} seconds; "
            f"source returned {available}."
        )
    return energy_mwh


def _adc_live_interval_estimate_mwh(
    site: dict,
    start: datetime,
    end: datetime,
) -> float:
    if (end - start).total_seconds() != 15 * 60:
        raise RuntimeError("ADC correction requires exactly one 15-minute interval.")
    try:
        average_kw = float(site["pvPowerAvg15MinKw"])
        live_kw = float(site["pvPowerKw"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "ADC requires both its current 15M AVG and live power values."
        ) from exc
    if (
        not math.isfinite(average_kw)
        or not math.isfinite(live_kw)
        or average_kw < 0
        or live_kw < 0
    ):
        raise RuntimeError("ADC returned an invalid 15M AVG or live power value.")
    estimated_average_mw = (average_kw + live_kw) / 2.0 / 1000.0
    return estimated_average_mw * 0.25

