from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, TimeoutError as PlaywrightTimeoutError, sync_playwright

from power_reading.scrapers.fusionsolar_scraper import PowerSnapshot


_ACTUAL_POWER_RE = re.compile(
    r"\bActual\s+power\b\s*(-?[0-9]+(?:[.,][0-9]+)?)\s*(MW|kW|W)\b",
    re.IGNORECASE,
)
_INITIAL_POWER_SETTLE_MS = 5_000
_STABLE_POWER_SAMPLE_COUNT = 3
_STABLE_POWER_ABSOLUTE_TOLERANCE_KW = 50.0
_STABLE_POWER_RELATIVE_TOLERANCE = 0.01


class NecaluxanReadOnlyError(RuntimeError):
    pass


class NecaluxanScraper:
    """Read Slatioara live power without interacting with any control commands."""

    def __init__(
        self,
        target_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        master_username: Optional[str] = None,
        master_password: Optional[str] = None,
        plant_name: Optional[str] = None,
        user_data_dir: str = ".playwright_profile_necaluxan",
        browser_timeout_ms: int = 60_000,
        headless: bool = False,
    ) -> None:
        self.target_url = target_url
        self.username = username
        self.password = password
        self.master_username = master_username
        self.master_password = master_password
        self.plant_name = plant_name or "RO-Slatioara 31.6MWp"
        self.user_data_dir = Path(user_data_dir)
        self.browser_timeout_ms = browser_timeout_ms
        self.headless = headless

    def scrape_once(self) -> PowerSnapshot:
        self._validate_credentials()
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            context: BrowserContext = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir.resolve()),
                headless=self.headless,
                ignore_https_errors=True,
                viewport={"width": 1800, "height": 1100},
                args=["--window-size=1800,1100"],
            )
            try:
                page = context.new_page()
                page.set_default_timeout(self.browser_timeout_ms)
                page.goto(self.target_url, wait_until="domcontentloaded")
                self._login_vcom(page)
                self._open_plant_cockpit(page)
                self._open_power_control(page)
                master_page = self._open_bluelog_master(context, page)
                self._login_bluelog(master_page)
                power_kw, raw_value = self._read_actual_power(master_page)
                return PowerSnapshot(
                    pv_kw=power_kw,
                    load_kw=None,
                    grid_kw=None,
                    timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                    source="meteocontrol-bluelog-actual-power",
                    raw_excerpt=f"Actual power {raw_value}",
                )
            finally:
                context.close()

    def _validate_credentials(self) -> None:
        missing = []
        if not self.username:
            missing.append("NECALUXAN_USERNAME")
        if not self.password:
            missing.append("NECALUXAN_PASSWORD")
        if not self.master_username:
            missing.append("NECALUXAN_MASTER_USERNAME")
        if not self.master_password:
            missing.append("NECALUXAN_MASTER_PASSWORD")
        if missing:
            raise NecaluxanReadOnlyError(
                f"Missing required Necaluxan credentials: {', '.join(missing)}."
            )

    def _login_vcom(self, page) -> None:
        legacy_form = page.locator("form[action*='/vcom/login/process']")
        if "/default/login/" in page.url:
            try:
                legacy_form.wait_for(state="attached", timeout=20_000)
            except PlaywrightTimeoutError as exc:
                raise NecaluxanReadOnlyError(
                    "The VCOM login page did not render its expected login form."
                ) from exc
        self._dismiss_cookie_banner(page)
        if legacy_form.count() > 0:
            legacy_form.locator("input[name='username']").fill(self.username)
            legacy_form.locator("input[name='password']").fill(self.password)
            legacy_form.locator("button[type='submit'][title='Login']").click()
            try:
                page.wait_for_url(
                    re.compile(r"auth\.meteocontrol\.com|/vcom/(?!default/login)"),
                    timeout=self.browser_timeout_ms,
                )
            except PlaywrightTimeoutError as exc:
                raise NecaluxanReadOnlyError(
                    f"The initial VCOM login did not advance (url={page.url})."
                ) from exc

        self._complete_meteocontrol_sso(page)
        try:
            page.wait_for_url(
                re.compile(r"www1\.meteocontrol\.de/vcom/(?!.*login)"),
                timeout=self.browser_timeout_ms,
            )
        except PlaywrightTimeoutError as exc:
            raise NecaluxanReadOnlyError(
                f"VCOM login did not reach the authenticated portal (url={page.url})."
            ) from exc

    def _dismiss_cookie_banner(self, page) -> None:
        necessary = page.get_by_role("button", name="Use necessary cookies only", exact=True)
        try:
            if necessary.count() == 1 and necessary.is_visible():
                necessary.click()
        except PlaywrightTimeoutError:
            pass

    def _complete_meteocontrol_sso(self, page) -> None:
        if "auth.meteocontrol.com" not in page.url:
            try:
                page.wait_for_url(re.compile(r"auth\.meteocontrol\.com|/vcom/"), timeout=15_000)
            except PlaywrightTimeoutError:
                pass
        if "auth.meteocontrol.com" not in page.url:
            return

        username = page.locator("input#username")
        username.wait_for(state="visible")
        username.fill(self.username)
        password = page.locator("input#password")
        if password.count() == 0:
            page.locator("input#kc-login[type='submit']").click()
            password.wait_for(state="visible")
        password.fill(self.password)
        page.locator("input#kc-login[type='submit']").click()

    def _open_plant_cockpit(self, page) -> None:
        row = page.locator("li[title]", has_text=self.plant_name)
        row.wait_for(state="visible")
        if row.count() != 1:
            raise NecaluxanReadOnlyError(
                f"Expected one VCOM system row for {self.plant_name!r}, found {row.count()}."
            )
        row.hover()
        cockpit_candidates = (
            row.locator("a[href*='/cockpit/']"),
            row.locator("a[title*='cockpit' i]"),
            page.locator("a[href*='/cockpit/']"),
            page.locator("a[title*='cockpit' i]"),
        )
        deadline = time.monotonic() + 10.0
        cockpit = None
        while cockpit is None and time.monotonic() < deadline:
            cockpit = _unique_visible_locator(cockpit_candidates)
            if cockpit is None:
                page.wait_for_timeout(500)
        if cockpit is None:
            raise NecaluxanReadOnlyError(
                f"The read-only cockpit link for {self.plant_name!r} was not uniquely identified."
            )
        cockpit.click()
        page.wait_for_url(re.compile(r"/cockpit/"))

    def _open_power_control(self, page) -> None:
        tab = page.locator("a[title='Power control'][href*='/ppc/']")
        tab.wait_for(state="visible")
        if tab.count() != 1:
            raise NecaluxanReadOnlyError(
                f"Expected one Power control navigation tab, found {tab.count()}."
            )
        tab.click()
        page.wait_for_url(re.compile(r"/ppc/"))

    def _open_bluelog_master(self, context, page):
        button = page.locator("button[title=\"Login blue'log Master\"]")
        button.wait_for(state="visible")
        if button.count() != 1:
            raise NecaluxanReadOnlyError(
                f"Expected one blue'Log Master login button, found {button.count()}."
            )
        try:
            with context.expect_page(timeout=self.browser_timeout_ms) as page_info:
                button.click()
            master_page = page_info.value
        except PlaywrightTimeoutError as exc:
            raise NecaluxanReadOnlyError("The blue'Log Master window did not open.") from exc
        master_page.set_default_timeout(self.browser_timeout_ms)
        return master_page

    def _login_bluelog(self, page) -> None:
        try:
            page.wait_for_url(re.compile(r"atlas\.sspcdn-a\.net"))
        except PlaywrightTimeoutError as exc:
            raise NecaluxanReadOnlyError(
                f"Unexpected blue'Log Master destination: {page.url}"
            ) from exc

        try:
            page.wait_for_function(
                """() =>
                    window.location.hash.includes('/overview/cockpit') ||
                    document.querySelector("input[data-test='username']") !== null
                """,
                timeout=self.browser_timeout_ms,
            )
        except PlaywrightTimeoutError as exc:
            raise NecaluxanReadOnlyError(
                f"blue'Log exposed neither its login form nor cockpit (url={page.url})."
            ) from exc
        if "/overview/cockpit" in page.url:
            return

        username = page.locator("input[data-test='username']")
        username.wait_for(state="visible")
        password = page.locator("input[data-test='password']")
        login = page.locator("button[data-test='login'][type='submit']")
        if username.count() != 1 or password.count() != 1 or login.count() != 1:
            raise NecaluxanReadOnlyError("The blue'Log login form was not uniquely identified.")
        username.fill(self.master_username)
        password.fill(self.master_password)
        login.click()
        try:
            page.wait_for_url(re.compile(r"#/overview/cockpit"))
        except PlaywrightTimeoutError as exc:
            raise NecaluxanReadOnlyError(
                f"blue'Log login did not reach the cockpit (url={page.url})."
            ) from exc

    def _read_actual_power(self, page) -> tuple[float, str]:
        deadline_ms = min(self.browser_timeout_ms, 30_000)
        page.get_by_text("Actual power", exact=True).wait_for(state="visible", timeout=deadline_ms)
        page.wait_for_timeout(_INITIAL_POWER_SETTLE_MS)
        deadline = time.monotonic() + (deadline_ms / 1000.0)
        observations: list[tuple[float, str]] = []
        while time.monotonic() < deadline:
            body_text = page.locator("body").inner_text()
            power_kw = extract_actual_power_kw(body_text)
            if power_kw is not None:
                match = _ACTUAL_POWER_RE.search(body_text)
                observations.append(
                    (power_kw, f"{match.group(1)} {match.group(2)}")
                )
                stable = select_stable_power_sample(observations)
                if stable is not None:
                    return stable
            page.wait_for_timeout(1000)
        raise NecaluxanReadOnlyError(
            "The blue'Log Actual power value did not stabilize before the timeout."
        )


def extract_actual_power_kw(text: str) -> Optional[float]:
    matches = list(_ACTUAL_POWER_RE.finditer(str(text or "")))
    if len(matches) != 1:
        return None
    value = _parse_number(matches[0].group(1))
    if value is None:
        return None
    unit = matches[0].group(2).lower()
    if unit == "mw":
        value *= 1000.0
    elif unit == "w":
        value /= 1000.0
    return max(value, 0.0)


def select_stable_power_sample(
    observations: list[tuple[float, str]],
) -> Optional[tuple[float, str]]:
    if len(observations) < _STABLE_POWER_SAMPLE_COUNT:
        return None
    window = observations[-_STABLE_POWER_SAMPLE_COUNT:]
    values = [sample[0] for sample in window]
    midpoint = sorted(values)[len(values) // 2]
    tolerance_kw = max(
        _STABLE_POWER_ABSOLUTE_TOLERANCE_KW,
        abs(midpoint) * _STABLE_POWER_RELATIVE_TOLERANCE,
    )
    if max(values) - min(values) > tolerance_kw:
        return None
    return window[-1]


def _parse_number(raw: str) -> Optional[float]:
    normalized = str(raw).strip().replace(" ", "")
    if not normalized:
        return None
    if "," in normalized and "." in normalized:
        decimal = "," if normalized.rfind(",") > normalized.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        normalized = normalized.replace(thousands, "").replace(decimal, ".")
    else:
        normalized = normalized.replace(",", ".")
    try:
        value = float(normalized)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _unique_visible_locator(candidates):
    for candidate in candidates:
        visible = []
        try:
            count = candidate.count()
        except Exception:
            continue
        for index in range(count):
            item = candidate.nth(index)
            try:
                if item.is_visible():
                    visible.append(item)
            except Exception:
                continue
        if len(visible) == 1:
            return visible[0]
    return None
