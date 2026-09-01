from __future__ import annotations

import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from playwright.sync_api import BrowserContext, TimeoutError as PlaywrightTimeoutError, sync_playwright

from power_reading.scrapers.fusionsolar_scraper import PowerSnapshot


_POWER_RE = re.compile(r"^\s*(-?[0-9]+(?:[.,][0-9]+)?)\s*(MW|kW|W)\s*$", re.IGNORECASE)
ISOLARCLOUD_CHART_TIMEZONE = ZoneInfo("Europe/Berlin")
MAX_LIVE_INTERVAL_AGE = timedelta(minutes=20)


class ISolarCloudReadOnlyError(RuntimeError):
    pass


class ISolarCloudScraper:
    """Read one plant-list power cell without opening plant or control pages."""

    def __init__(
        self,
        target_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        plant_name: Optional[str] = None,
        user_data_dir: str = ".playwright_profile_isolarcloud",
        browser_timeout_ms: int = 60_000,
        headless: bool = False,
        credential_env_prefix: str = "MM_MV",
    ) -> None:
        self.target_url = target_url
        self.username = username
        self.password = password
        self.plant_name = plant_name or "Reghin"
        self.user_data_dir = Path(user_data_dir)
        self.browser_timeout_ms = browser_timeout_ms
        self.headless = headless
        self.credential_env_prefix = credential_env_prefix

    def scrape_once(self) -> PowerSnapshot:
        self._validate_credentials()
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            context: BrowserContext = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir.resolve()),
                headless=self.headless,
                viewport={"width": 1800, "height": 1000},
                args=["--window-size=1800,1000"],
            )
            try:
                page = context.new_page()
                page.set_default_timeout(self.browser_timeout_ms)
                page.goto(self.target_url, wait_until="domcontentloaded")
                self._accept_cookies(page)
                self._login_if_required(page)
                power_kw, raw_value = self._read_realtime_power_kw(page)
                return PowerSnapshot(
                    pv_kw=power_kw,
                    load_kw=None,
                    grid_kw=None,
                    timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                    source=f"isolarcloud-plant-list-real-time-power@{self.plant_name}",
                    raw_excerpt=f"{self.plant_name} | Real-time power {raw_value}",
                )
            finally:
                context.close()

    def read_interval_energy(self, *, start: datetime, end: datetime) -> float:
        self._validate_credentials()
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context: BrowserContext = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir.resolve()),
                headless=self.headless,
                viewport={"width": 1800, "height": 1000},
                args=["--window-size=1800,1000"],
            )
            try:
                page = context.new_page()
                page.set_default_timeout(self.browser_timeout_ms)
                page.goto(self.target_url, wait_until="domcontentloaded")
                self._accept_cookies(page)
                self._login_if_required(page)
                self._find_plant_link(page).click()
                page.wait_for_url(re.compile(r"#/plantDetail/overView"), timeout=self.browser_timeout_ms)
                canvas = page.locator("canvas").first
                canvas.wait_for(state="visible", timeout=self.browser_timeout_ms)
                canvas.scroll_into_view_if_needed()
                box = canvas.bounding_box()
                if not box:
                    raise ISolarCloudReadOnlyError("iSolarCloud history chart has no visible bounds.")

                local_tz = ZoneInfo("Europe/Bucharest")
                local_start = start.astimezone(local_tz)
                local_end = end.astimezone(local_tz)
                points: dict[datetime, float] = {}
                chart = page.locator("[_echarts_instance_]").first
                right = box["x"] + box["width"] - 5
                left = box["x"] + 35
                x = right
                while x >= left and not (local_start in points and local_end in points):
                    page.mouse.move(x, box["y"] + box["height"] / 2)
                    page.wait_for_timeout(35)
                    parsed = _parse_isolar_chart_tooltip(chart.inner_text())
                    if parsed is not None:
                        hour, minute, power_mw = parsed
                        source_timestamp = local_start.astimezone(ISOLARCLOUD_CHART_TIMEZONE).replace(
                            hour=hour, minute=minute, second=0, microsecond=0
                        )
                        timestamp = source_timestamp.astimezone(local_tz)
                        points[timestamp] = power_mw
                    x -= 3
                try:
                    return _integrate_isolar_interval(
                        sorted(points.items()), local_start, local_end, self.plant_name
                    )
                except ISolarCloudReadOnlyError as exc:
                    if "missing a completed-interval boundary" not in str(exc):
                        raise
                    page.goto(self.target_url, wait_until="domcontentloaded")
                    self._accept_cookies(page)
                    self._login_if_required(page)
                    power_kw, _ = self._read_realtime_power_kw(page)
                    return _live_power_interval_estimate_mwh(
                        power_kw,
                        local_start,
                        local_end,
                        observed_at=datetime.now(tz=timezone.utc),
                        plant_name=self.plant_name,
                    )
            finally:
                context.close()

    def _read_realtime_power_kw(self, page) -> tuple[float, str]:
        plant_link = self._find_plant_link(page)
        row = plant_link.locator("xpath=ancestor::tr[1]")
        table = row.locator(
            "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), "
            "' el-table ')][1]"
        )
        headers = table.locator("thead th").all_inner_texts()
        cells = row.locator("td").all_inner_texts()
        return extract_realtime_power_kw(headers, cells, self.plant_name)

    def _validate_credentials(self) -> None:
        missing = []
        if not self.username:
            missing.append(f"{self.credential_env_prefix}_USERNAME")
        if not self.password:
            missing.append(f"{self.credential_env_prefix}_PASSWORD")
        if missing:
            raise ISolarCloudReadOnlyError(
                f"Missing required iSolarCloud credentials: {', '.join(missing)}."
            )

    def _accept_cookies(self, page) -> None:
        agree = page.get_by_role("button", name="Yes, I agree", exact=True)
        try:
            if agree.count() == 1 and agree.is_visible():
                agree.click()
        except PlaywrightTimeoutError:
            pass

    def _login_if_required(self, page) -> None:
        account = page.get_by_placeholder("Account", exact=True)
        password = page.get_by_placeholder("Password", exact=True)
        if "#/plantList" not in page.url:
            try:
                account.wait_for(state="visible", timeout=20_000)
            except PlaywrightTimeoutError as exc:
                raise ISolarCloudReadOnlyError(
                    f"iSolarCloud exposed neither its login form nor plant list (url={page.url})."
                ) from exc
        if "#/plantList" not in page.url:
            if account.count() != 1 or password.count() != 1:
                raise ISolarCloudReadOnlyError(
                    "The iSolarCloud login fields were not uniquely identified."
                )
            account.fill(self.username)
            password.fill(self.password)
            login = page.get_by_role("button", name="Login", exact=True)
            if login.count() != 1:
                raise ISolarCloudReadOnlyError(
                    f"Expected one iSolarCloud Login button, found {login.count()}."
                )
            login.click()

        try:
            page.wait_for_url(re.compile(r"#/plantList(?:$|[?/])"), timeout=self.browser_timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise ISolarCloudReadOnlyError(
                f"iSolarCloud login did not reach the plant list (url={page.url})."
            ) from exc

    def _find_plant_link(self, page):
        page.get_by_text("Real-time power", exact=True).wait_for(state="visible")
        wanted = _normalized_text(self.plant_name)
        deadline = time.monotonic() + (self.browser_timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            links = page.locator("a[href*='#/plantDetail/overView']")
            matches = []
            for index in range(links.count()):
                link = links.nth(index)
                lines = [line.strip() for line in link.inner_text().splitlines() if line.strip()]
                if lines and _normalized_text(lines[0]) == wanted and link.is_visible():
                    matches.append(link)
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                break
            page.wait_for_timeout(500)
        count = len(matches)
        if count != 1:
            raise ISolarCloudReadOnlyError(
                f"Expected one iSolarCloud plant row for {self.plant_name!r}, found "
                f"{count}."
            )
        return matches[0]


def extract_realtime_power_kw(
    headers: list[str],
    cells: list[str],
    plant_name: str,
) -> tuple[float, str]:
    normalized_headers = [_normalized_text(header) for header in headers]
    matching_columns = [
        index
        for index, header in enumerate(normalized_headers)
        if header == "real-time power"
    ]
    if len(matching_columns) != 1:
        raise ISolarCloudReadOnlyError(
            "The iSolarCloud table did not expose one Real-time power column."
        )
    column = matching_columns[0]
    if column >= len(cells):
        raise ISolarCloudReadOnlyError(
            f"The iSolarCloud {plant_name} row does not match the visible table columns."
        )

    plant_lines = [line.strip() for line in str(cells[0]).splitlines() if line.strip()]
    if not plant_lines or _normalized_text(plant_lines[0]) != _normalized_text(plant_name):
        raise ISolarCloudReadOnlyError(
            f"The iSolarCloud power row is not the requested plant {plant_name!r}."
        )

    raw_value = str(cells[column]).strip()
    match = _POWER_RE.fullmatch(raw_value)
    if not match:
        raise ISolarCloudReadOnlyError(
            f"The iSolarCloud Real-time power value is invalid: {raw_value!r}."
        )
    value = _parse_number(match.group(1))
    if value is None or value < 0:
        raise ISolarCloudReadOnlyError(
            f"The iSolarCloud Real-time power value is invalid: {raw_value!r}."
        )
    unit = match.group(2).lower()
    if unit == "mw":
        value *= 1000.0
    elif unit == "w":
        value /= 1000.0
    return value, raw_value


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _parse_number(raw: str) -> Optional[float]:
    normalized = str(raw).strip().replace(" ", "").replace(",", ".")
    try:
        value = float(normalized)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _parse_isolar_chart_tooltip(text: str) -> Optional[tuple[int, int, float]]:
    match = re.search(
        r"\b(\d{1,2}):(\d{2})\b.*?PV\s*[:\uff1a]\s*"
        r"([0-9]+(?:[.,][0-9]+)*)\s*(MW|kW|W)\b",
        str(text or ""),
        re.I | re.S,
    )
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    raw_value = match.group(3).replace(" ", "")
    if "," in raw_value and "." in raw_value:
        raw_value = raw_value.replace(",", "")
    elif "," in raw_value:
        raw_value = raw_value.replace(",", ".")
    try:
        value = float(raw_value)
    except ValueError:
        return None
    unit = match.group(4).lower()
    if unit == "kw":
        value /= 1000.0
    elif unit == "w":
        value /= 1_000_000.0
    if not math.isfinite(value) or value < 0 or hour > 23 or minute > 59:
        return None
    return hour, minute, value


def _integrate_isolar_interval(
    points: list[tuple[datetime, float]],
    start: datetime,
    end: datetime,
    plant_name: str,
) -> float:
    by_timestamp = dict(points)
    if start not in by_timestamp or end not in by_timestamp:
        available = "no timestamped points"
        if points:
            available = f"points from {min(by_timestamp).isoformat()} to {max(by_timestamp).isoformat()}"
        raise ISolarCloudReadOnlyError(
            f"iSolarCloud {plant_name} chart is missing a completed-interval boundary; "
            f"source returned {available}."
        )
    ordered = sorted(
        (timestamp, power)
        for timestamp, power in points
        if start <= timestamp <= end
    )
    energy_mwh = 0.0
    for (left_time, left_power), (right_time, right_power) in zip(ordered, ordered[1:]):
        if right_time - left_time > timedelta(minutes=15):
            raise ISolarCloudReadOnlyError(
                f"iSolarCloud {plant_name} chart has a gap in the completed interval."
            )
        energy_mwh += (
            (left_power + right_power) / 2.0
            * (right_time - left_time).total_seconds()
            / 3600.0
        )
    if not math.isfinite(energy_mwh) or energy_mwh < 0:
        raise ISolarCloudReadOnlyError(
            f"iSolarCloud {plant_name} interval energy is invalid."
        )
    return energy_mwh


def _live_power_interval_estimate_mwh(
    power_kw: float,
    start: datetime,
    end: datetime,
    *,
    observed_at: datetime,
    plant_name: str,
) -> float:
    if any(value.tzinfo is None for value in (start, end, observed_at)):
        raise ISolarCloudReadOnlyError(
            f"iSolarCloud {plant_name} interval estimate requires timezone-aware timestamps."
        )
    if end - start != timedelta(minutes=15):
        raise ISolarCloudReadOnlyError(
            f"iSolarCloud {plant_name} interval estimate requires exactly 15 minutes."
        )
    age = observed_at.astimezone(timezone.utc) - end.astimezone(timezone.utc)
    if age < timedelta(0) or age > MAX_LIVE_INTERVAL_AGE:
        raise ISolarCloudReadOnlyError(
            f"iSolarCloud {plant_name} cannot estimate an old interval from live power."
        )
    if not math.isfinite(power_kw) or power_kw < 0:
        raise ISolarCloudReadOnlyError(
            f"iSolarCloud {plant_name} live power is invalid."
        )
    return power_kw / 1000.0 * 0.25
