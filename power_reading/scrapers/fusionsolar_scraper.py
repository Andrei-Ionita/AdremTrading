from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, TimeoutError as PlaywrightTimeoutError, sync_playwright
cv2 = None
np = None

RapidOCR = None


def _get_cv2_np():
    global cv2, np
    if cv2 is not None and np is not None:
        return cv2, np
    try:
        import cv2 as cv2_mod
        import numpy as np_mod
    except Exception:  # noqa: BLE001
        return None, None
    cv2 = cv2_mod
    np = np_mod
    return cv2, np


def _get_rapidocr_class():
    global RapidOCR
    if RapidOCR is not None:
        return RapidOCR
    try:
        from rapidocr_onnxruntime import RapidOCR as rapid_ocr
    except Exception:  # noqa: BLE001
        return None
    RapidOCR = rapid_ocr
    return RapidOCR


LABELS = ("PV", "Load", "Grid")
NUM_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)")


@dataclass
class PowerSnapshot:
    pv_kw: Optional[float]
    load_kw: Optional[float]
    grid_kw: Optional[float]
    timestamp_utc: str
    source: str
    raw_excerpt: str


class FusionSolarScraper:
    def __init__(
        self,
        target_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        plant_name: Optional[str] = None,
        region_name: Optional[str] = None,
        use_saved_session_only: bool = False,
        user_data_dir: str = ".playwright_profile",
        browser_timeout_ms: int = 45_000,
        headless: bool = False,
    ) -> None:
        self.target_url = target_url
        self.username = username
        self.password = password
        self.plant_name = plant_name
        self.region_name = region_name
        self.use_saved_session_only = use_saved_session_only
        self.user_data_dir = Path(user_data_dir)
        self.browser_timeout_ms = browser_timeout_ms
        self.headless = headless

    def scrape_once(self) -> PowerSnapshot:
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            context: BrowserContext = p.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir.resolve()),
                headless=self.headless,
                viewport={"width": 1920, "height": 1200},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=1920,1200",
                ],
            )
            try:
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
                page = context.new_page()
                page.set_default_timeout(self.browser_timeout_ms)
                page.goto(self.target_url, wait_until="domcontentloaded")
                if not self.use_saved_session_only:
                    self._maybe_login(page)

                if self._force_table_current_power():
                    table_kw = self._extract_current_power_with_plants_fallback(page)
                    if table_kw is not None:
                        return PowerSnapshot(
                            pv_kw=table_kw,
                            load_kw=None,
                            grid_kw=None,
                            timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                            source="table-current-power-forced",
                            raw_excerpt=_compact_excerpt(page.locator("body").first.inner_text()),
                        )

                # Some tenant pages expose reliable "Current Power" in list tables before opening a plant.
                pre_table_kw = self._extract_current_power_from_table(page)
                if pre_table_kw is not None:
                    return PowerSnapshot(
                        pv_kw=pre_table_kw,
                        load_kw=None,
                        grid_kw=None,
                        timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                        source="table-current-power-preopen",
                        raw_excerpt=_compact_excerpt(page.locator("body").first.inner_text()),
                    )

                self._open_plant_if_needed(page)
                if self.headless:
                    self._stabilize_headless_page(page)

                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeoutError:
                    # The page can keep background requests open; continue with current DOM.
                    pass

                text = page.locator("body").first.inner_text()
                nominal_kw = _extract_inverter_nominal_power_kw(text)
                device_table_kw = self._extract_active_power_from_device_table(page)
                pv_kw = _extract_kw(text, "PV")
                load_kw = _extract_kw(text, "Load")
                grid_kw = _extract_kw(text, "Grid")
                source = "text-pattern"
                if pv_kw is None and load_kw is None and grid_kw is None:
                    # Prefer PV/Load/Grid from the flow diagram (OCR) before generic
                    # "Active power/Current Power" fallback, which can point to a
                    # different metric on some tenant layouts.
                    pv_kw, load_kw, grid_kw = _extract_flow_kw_ocr(page)
                    pv_kw = _normalize_ocr_kw_against_nominal(pv_kw, nominal_kw)
                    load_kw = _normalize_ocr_kw_against_nominal(load_kw, nominal_kw)
                    grid_kw = _normalize_ocr_kw_against_nominal(grid_kw, nominal_kw)
                    if pv_kw is not None or load_kw is not None or grid_kw is not None:
                        source = "ocr-flow"
                        pv_kw, used_active_power = _select_overview_pv_kw(
                            pv_kw,
                            text,
                            nominal_kw,
                            device_table_kw,
                        )
                        if used_active_power:
                            source = "device-table-active-power" if device_table_kw is not None and pv_kw == device_table_kw else "active-power-text"
                    else:
                        # Some FusionSolar layouts expose only "Active power" on overview.
                        active_kw = _extract_active_power_kw(text)
                        active_kw = _sanitize_active_power_kw(active_kw, nominal_kw, device_table_kw)
                        if active_kw is not None:
                            pv_kw = active_kw
                            source = "device-table-active-power" if device_table_kw is not None and active_kw == device_table_kw else "active-power-text"
                            return PowerSnapshot(
                                pv_kw=pv_kw,
                                load_kw=load_kw,
                                grid_kw=grid_kw,
                                timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                                source=source,
                                raw_excerpt=_compact_excerpt(text),
                            )
                        table_kw = self._extract_current_power_from_table(page)
                        if table_kw is not None:
                            pv_kw = table_kw
                            source = "table-current-power"
                        else:
                            if self.headless:
                                page.wait_for_timeout(2_000)
                                table_kw = self._extract_current_power_from_table(page)
                                if table_kw is not None:
                                    pv_kw = table_kw
                                    source = "table-current-power"
                            if source != "table-current-power":
                                source = "unmatched"

                if source == "unmatched":
                    # Useful for diagnosing selector or auth drift.
                    page.wait_for_timeout(2_000)

                return PowerSnapshot(
                    pv_kw=pv_kw,
                    load_kw=load_kw,
                    grid_kw=grid_kw,
                    timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                    source=source,
                    raw_excerpt=_compact_excerpt(text),
                )
            finally:
                context.close()

    def _force_table_current_power(self) -> bool:
        plant_name = (self.plant_name or "").strip().lower()
        return plant_name == "elnet biomasa.gr"

    def _extract_current_power_with_plants_fallback(self, page) -> Optional[float]:
        self._open_plants_page(page)
        table_kw = self._extract_current_power_from_table(page)
        if table_kw is not None:
            return table_kw
        page.wait_for_timeout(1500)
        return self._extract_current_power_from_table(page)

    def _open_plants_page(self, page) -> None:
        nav_candidates = (
            page.get_by_role("link", name=re.compile(r"^\s*Plants\s*$", re.I)),
            page.get_by_role("link", name=re.compile(r"^\s*Centrale\s*$", re.I)),
            page.get_by_text(re.compile(r"^\s*Plants\s*$", re.I), exact=False),
            page.get_by_text(re.compile(r"^\s*Centrale\s*$", re.I), exact=False),
            page.locator("a[href*='plant']"),
            page.locator("span:has-text('Plants')"),
            page.locator("span:has-text('Centrale')"),
        )
        clicked = False
        for loc in nav_candidates:
            try:
                if loc.count() <= 0:
                    continue
                loc.first.click(timeout=1500)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            script = """
            () => {
              const norm = (s) => (s || '').trim().toLowerCase();
              const candidates = Array.from(document.querySelectorAll('a, span, div, li, button'))
                .filter(el => el && el.offsetParent !== null);
              const target = candidates.find(el => {
                const t = norm(el.textContent || '');
                return t === 'plants' || t === 'centrale';
              });
              if (!target) return false;
              target.click();
              return true;
            }
            """
            try:
                clicked = bool(page.evaluate(script))
            except Exception:
                clicked = False
        try:
            page.wait_for_timeout(1500)
            page.locator("table tbody tr").first.wait_for(timeout=10_000)
            page.get_by_text(re.compile(r"Current Power \(kW\)|Putere activÄƒ \(kW\)", re.I), exact=False).first.wait_for(timeout=5_000)
        except Exception:
            pass

    def _stabilize_headless_page(self, page) -> None:
        # FusionSolar can load table data lazily in headless; give it a deterministic settle step.
        try:
            page.wait_for_timeout(1200)
            # Try to trigger table refresh if a search button exists.
            search_btns = (
                page.locator("button:has-text('Search')"),
                page.locator("span:has-text('Search')"),
                page.get_by_role("button", name=re.compile(r"^\s*Search\s*$", re.I)),
            )
            for btn in search_btns:
                if btn.count() <= 0:
                    continue
                try:
                    btn.first.click(timeout=800)
                    break
                except Exception:
                    continue
            page.wait_for_timeout(1200)
        except Exception:
            pass

    def _maybe_login(self, page) -> None:
        username_input = page.locator("#username")
        password_input = page.locator("#value")
        login_btn = page.locator(".loginBtn")

        on_login_page = "login.action" in page.url.lower() or username_input.count() > 0
        if not on_login_page:
            return

        if not (self.username and self.password):
            return

        username_input.first.fill(self.username)
        password_input.first.fill(self.password)
        region_name = (self.region_name or "").strip()
        if not region_name:
            self._click_login(page, login_btn, password_input)
        else:
            page.wait_for_timeout(400)
            # Attempt 1: select region first when possible, then login.
            self._select_region_on_login(page)
            self._click_login(page, login_btn, password_input)

            # Attempt 2: if still on login page or explicit banner appears, select region and login again.
            still_login = ("login.action" in page.url.lower()) or (username_input.count() > 0)
            need_retry = still_login or page.get_by_text("Select a region and log in again", exact=False).count() > 0
            if need_retry:
                page.wait_for_timeout(900)
                self._select_region_on_login(page)
                self._click_login(page, login_btn, password_input)
                if page.get_by_text("Select a region and log in again", exact=False).count() > 0:
                    raise RuntimeError(f"Failed to select region '{region_name}' on FusionSolar login.")

        try:
            page.wait_for_url(re.compile(r".*/view/station/.*"), timeout=25_000)
        except PlaywrightTimeoutError:
            pass

        page.wait_for_timeout(3_000)

    def _click_login(self, page, login_btn, password_input) -> None:
        if login_btn.count() > 0:
            login_btn.first.click()
        else:
            password_input.first.press("Enter")
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except PlaywrightTimeoutError:
            pass

    def _select_region_on_login(self, page) -> bool:
        region_name = (self.region_name or "").strip()
        if not region_name:
            return True
        target = region_name.lower()
        for _ in range(6):
            if self._selected_region(page) == target:
                return True
            self._open_region_dropdown(page)
            if self._js_open_and_pick_region(page, region_name):
                page.wait_for_timeout(300)
                if self._selected_region(page) == target:
                    return True
                return True
            if self._click_region_option(page, region_name):
                page.wait_for_timeout(350)
                # Accept click success; some FusionSolar DOM variants don't expose selected value reliably.
                if self._selected_region(page) == target:
                    return True
                return True
            if self._force_select_region(region_name, page):
                page.wait_for_timeout(400)
                if self._selected_region(page) == target:
                    return True
                return True
            if self._region_keyboard_select(page, region_name):
                page.wait_for_timeout(400)
                return True
            if self._region_cycle_select(page, region_name):
                page.wait_for_timeout(400)
                return True
            page.wait_for_timeout(300)
        return False

    def _js_open_and_pick_region(self, page, region_name: str) -> bool:
        script = """
        (targetName) => {
          const norm = (s) => (s || '').trim().toLowerCase();
          const isVisible = (el) => !!el && !!el.offsetParent;

          const regionInputs = Array.from(document.querySelectorAll('input'))
            .filter(el => isVisible(el) && /^region\\d{3}$/i.test((el.value || '').trim()));
          const regionField = regionInputs[0] || null;
          if (!regionField) return false;

          // Open dropdown list.
          regionField.focus();
          regionField.click();
          regionField.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
          regionField.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));

          const options = Array.from(
            document.querySelectorAll('li, [role="option"], .el-select-dropdown__item, .el-select-dropdown li, .el-select-dropdown div')
          ).filter(isVisible);
          if (!options.length) return false;

          let target = options.find(el => norm(el.textContent) === norm(targetName));
          if (!target && norm(targetName) === 'region004') {
            target = options.find(el => norm(el.textContent).includes('region004'));
            if (!target && options.length >= 2) target = options[1];
          }
          if (!target) return false;

          target.scrollIntoView({ block: 'nearest' });
          target.click();
          target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
          target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
          return true;
        }
        """
        try:
            return bool(page.evaluate(script, region_name))
        except Exception:
            return False

    def _open_region_dropdown(self, page) -> None:
        # Step 1: click the region selector so options list becomes visible.
        switchers = (
            page.locator("input[value='region003']:visible"),
            page.locator("input[value='region004']:visible"),
            page.locator("input[value*='region00']:visible"),
            page.locator(".el-input__suffix:visible"),
            page.locator(".el-select__caret:visible"),
            page.locator("i[class*='caret']:visible"),
            page.locator("div:visible:has-text('region003')"),
            page.locator("div:visible:has-text('region004')"),
            page.locator("span:visible:has-text('region003')"),
            page.locator("span:visible:has-text('region004')"),
            page.locator(".el-select"),
            page.get_by_role("combobox"),
            page.locator("[role='combobox']"),
            page.locator("[aria-haspopup='listbox']"),
        )
        for _ in range(3):
            for sw in switchers:
                if sw.count() == 0:
                    continue
                try:
                    sw.first.click(force=True)
                    page.wait_for_timeout(250)
                    if self._is_region_list_open(page):
                        return
                except Exception:
                    continue

    def _is_region_list_open(self, page) -> bool:
        try:
            options = page.locator("li:visible").filter(has_text=re.compile(r"^\s*region00[34]\s*$", re.I))
            if options.count() > 0:
                return True
        except Exception:
            pass
        return False

    def _region_keyboard_select(self, page, region_name: str) -> bool:
        # Practical fallback for compact region picker controls.
        target = region_name.strip().lower()
        fields = (
            page.locator("input[value='region003']:visible"),
            page.locator("input[value='region004']:visible"),
            page.locator("input[value*='region00']:visible"),
            page.locator("div:visible:has-text('region003')"),
            page.locator("div:visible:has-text('region004')"),
            page.locator("span:visible:has-text('region003')"),
            page.locator("span:visible:has-text('region004')"),
            page.locator(".el-select"),
        )
        for field in fields:
            try:
                if field.count() == 0:
                    continue
                field.first.click(force=True)
                page.wait_for_timeout(120)
                if self._selected_region(page) == target:
                    return True
                # Open list and move to next option (region003 -> region004).
                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(100)
                page.keyboard.press("Enter")
                page.wait_for_timeout(300)
                if self._selected_region(page) == target:
                    return True
                # Extra fallback: repeat once in case first down only opened dropdown.
                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(100)
                page.keyboard.press("Enter")
                page.wait_for_timeout(300)
                if self._selected_region(page) == target:
                    return True
            except Exception:
                continue
        return False

    def _region_cycle_select(self, page, region_name: str) -> bool:
        target = region_name.lower()
        selectors = (
            page.locator("div:visible:has-text('region003')"),
            page.locator("div:visible:has-text('region004')"),
            page.locator("span:visible:has-text('region003')"),
            page.locator("span:visible:has-text('region004')"),
        )
        for sel in selectors:
            try:
                if sel.count() == 0:
                    continue
                sel.first.click(force=True)
                page.keyboard.press("ArrowDown")
                page.keyboard.press("Enter")
                page.wait_for_timeout(250)
                if self._selected_region(page) == target:
                    return True
            except Exception:
                continue
        return False

    def _selected_region(self, page) -> str:
        # Primary source: the read-only input that holds the selected region.
        input_candidates = (
            page.locator("input[value='region003']:visible"),
            page.locator("input[value='region004']:visible"),
            page.locator("input[value*='region00']:visible"),
        )
        for inp in input_candidates:
            try:
                if inp.count() <= 0:
                    continue
                value = (inp.first.input_value() or "").strip().lower()
                if re.fullmatch(r"region\d{3}", value):
                    return value
            except Exception:
                continue

        script = """
        () => {
          const nodes = Array.from(document.querySelectorAll('div, span, input'))
            .filter(el => el && el.offsetParent !== null);
          const hits = nodes
            .map(el => ((el.innerText || el.value || '') + '').trim())
            .filter(txt => /^region\\d{3}$/i.test(txt));
          if (!hits.length) return '';
          // Prefer the first visible selected-like value (not from options list if possible).
          return (hits[0] || '').toLowerCase();
        }
        """
        try:
            value = page.evaluate(script) or ""
            return str(value).strip().lower()
        except Exception:
            return ""

    def _force_select_region(self, region_name: str, page) -> bool:
        # Deterministic selection for FusionSolar login dropdown.
        target = region_name.lower()
        try:
            strict_visible = page.locator("li:visible").filter(
                has_text=re.compile(rf"^\s*{re.escape(region_name)}\s*$", re.I)
            )
            if strict_visible.count() > 0:
                strict_visible.first.click(force=True)
                page.wait_for_timeout(250)
                if self._selected_region(page) == target:
                    return True
        except Exception:
            pass

        script = """
        (targetName) => {
          const norm = (s) => (s || '').trim().toLowerCase();
          const items = Array.from(document.querySelectorAll('li, div[role="option"], .el-select-dropdown__item'))
            .filter(el => el && el.offsetParent !== null);
          if (!items.length) return false;

          let target = items.find(el => norm(el.textContent) === norm(targetName));
          if (!target && norm(targetName) === 'region004') {
            target = items.find(el => norm(el.textContent).includes('region004'));
            if (!target && items.length >= 2) target = items[1];
          }
          if (!target) return false;
          target.click();
          return true;
        }
        """
        try:
            if bool(page.evaluate(script, region_name)):
                page.wait_for_timeout(250)
                if self._selected_region(page) == target:
                    return True
        except Exception:
            pass

        # Positional fallback for the specific 2-option region dropdown (region003/region004).
        if region_name.lower() == "region004":
            try:
                anchor_candidates = (
                    page.locator("input[value='region003']:visible"),
                    page.locator("input[value*='region00']:visible"),
                    page.locator("div:visible:has-text('region003')"),
                )
                anchor = None
                for cand in anchor_candidates:
                    if cand.count() > 0:
                        anchor = cand.first
                        break
                if anchor is None:
                    return False
                box = anchor.bounding_box()
                if box:
                    # Click below the selector field where the second option is rendered.
                    x = box["x"] + min(80, box["width"] * 0.25)
                    y = box["y"] + (box["height"] * 2.2)
                    page.mouse.click(x, y)
                    page.wait_for_timeout(300)
                    if self._selected_region(page) == target:
                        return True
            except Exception:
                pass
        return False

    def _click_region_option(self, page, region_name: str) -> bool:
        locators = (
            page.locator(f"li:visible:text-is('{region_name}')"),
            page.locator(f"li:visible:has-text('{region_name}')"),
            page.locator(f"div[role='option']:visible:has-text('{region_name}')"),
            page.get_by_role("option", name=re.compile(rf"^\s*{re.escape(region_name)}\s*$", re.I)),
            page.get_by_text(region_name, exact=True),
        )
        for loc in locators:
            try:
                if loc.count() == 0:
                    continue
                loc.first.click(force=True)
                page.wait_for_timeout(250)
                return True
            except Exception:
                continue

        # DOM-level fallback for stubborn dropdowns.
        script = """
        (name) => {
          const candidates = Array.from(document.querySelectorAll('li, div[role="option"], .el-select-dropdown__item, span, div'));
          const match = candidates.find(el => (el.textContent || '').trim().toLowerCase() === name.toLowerCase());
          if (!match) return false;
          match.click();
          return true;
        }
        """
        try:
            return bool(page.evaluate(script, region_name))
        except Exception:
            return False

    def _open_plant_if_needed(self, page) -> None:
        plant_name = (self.plant_name or "").strip()
        if not plant_name:
            return

        # If we're already on the requested station overview page, do nothing.
        # Saved browser sessions can reopen the last viewed station, which may
        # be a different plant on shared FusionSolar accounts.
        if page.locator("text=Active power").count() > 0 and page.locator("text=PV").count() > 0:
            try:
                body_text = page.locator("body").first.inner_text(timeout=3_000)
            except Exception:
                body_text = ""
            if re.search(re.escape(plant_name), body_text, re.I):
                return
            self._open_plants_page(page)

        # First, prefer deterministic selection from the assets table "Plant Name" column.
        if self._click_plant_from_assets_table(page, plant_name):
            try:
                page.wait_for_timeout(1_200)
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass
            return

        candidates = (
            page.locator(f"tr:has-text('{plant_name}')"),
            page.get_by_text(re.compile(re.escape(plant_name), re.I), exact=False),
            page.get_by_role("link", name=plant_name, exact=True),
            page.get_by_role("link", name=plant_name),
            page.locator(f"a:has-text('{plant_name}')"),
            page.locator(f"text={plant_name}"),
        )

        clicked = False
        for locator in candidates:
            if locator.count() > 0:
                try:
                    locator.first.click()
                    clicked = True
                    break
                except Exception:
                    continue

        # Fallback for tenant list pages: click last visible plant row.
        if not clicked:
            row_sets = (
                page.locator("table tbody tr"),
                page.locator("tbody tr"),
                page.locator("tr"),
            )
            for rows in row_sets:
                try:
                    count = rows.count()
                except Exception:
                    continue
                if count <= 0:
                    continue
                last_row = rows.nth(count - 1)
                row_targets = (
                    last_row.locator("a"),
                    last_row.locator("td"),
                    last_row,
                )
                for target in row_targets:
                    try:
                        if target.count() <= 0:
                            continue
                        target.first.click()
                        clicked = True
                        break
                    except Exception:
                        continue
                if clicked:
                    break

        if not clicked:
            return

        try:
            page.wait_for_url(re.compile(r".*/view/station/.*"), timeout=25_000)
        except PlaywrightTimeoutError:
            pass

        try:
            page.locator("text=Active power").first.wait_for(timeout=25_000)
        except PlaywrightTimeoutError:
            pass

    def _extract_current_power_from_table(self, page) -> Optional[float]:
        # FusionSolar tenant list pages expose live power in "Current Power (kW)".
        plant_name = (self.plant_name or "").strip().lower()
        script = """
        (plantName) => {
          const norm = (s) => (s || '').trim().toLowerCase();
          const rows = Array.from(document.querySelectorAll('table tbody tr'));
          if (!rows.length) return null;

          const parseNum = (txt) => {
            const rawMatch = String(txt || '').match(/-?\\d+(?:[.,]\\d+)*/);
            if (!rawMatch) return null;
            let raw = rawMatch[0];
            if (/^-?\\d{1,3}(?:,\\d{3})+(?:\\.\\d+)?$/.test(raw)) {
              raw = raw.replace(/,/g, '');
            } else if (/^-?\\d{1,3}(?:\\.\\d{3})+(?:,\\d+)?$/.test(raw)) {
              raw = raw.replace(/\\./g, '').replace(',', '.');
            } else {
              raw = raw.replace(',', '.');
            }
            const num = Number(raw);
            return Number.isFinite(num) ? num : null;
          };

          const exactMatches = rows.filter((row) => {
            const t = norm(row.textContent || '');
            return !plantName || t.includes(plantName);
          });

          const matched = exactMatches.length
            ? exactMatches
            : (plantName.includes('horeco')
                ? rows.filter(row => norm(row.textContent || '').includes('horeco'))
                : []);
          if (plantName && !matched.length) return null;
          const row = matched.length ? matched[matched.length - 1] : rows[rows.length - 1];
          const cells = Array.from(row.querySelectorAll('td'));
          if (!cells.length) return null;

          // Preferred: resolve the "Current Power" column index from table headers.
          const table = row.closest('table');
          const headers = table
            ? Array.from(table.querySelectorAll('thead th, tr th'))
            : [];
          let currentPowerIdx = -1;
          for (let i = 0; i < headers.length; i++) {
            const htxt = norm(headers[i].textContent || '');
            if (htxt.includes('current power')) {
              currentPowerIdx = i;
              break;
            }
          }
          if (currentPowerIdx >= 0 && currentPowerIdx < cells.length) {
            const v = parseNum(cells[currentPowerIdx].textContent || '');
            if (v !== null && v >= 0) return v;
          }

          // Header-independent approach: first numeric cell after battery/status columns often holds current power.
          // Prefer explicit kW-like value from row.
          for (const td of cells) {
            const v = parseNum(td.textContent || '');
            if (v !== null && v >= 0) {
              const txt = norm(td.textContent || '');
              if (txt.includes('kw') || txt.includes('k w')) return v;
            }
          }

          // Fallback for known layout from screenshot: Current Power is near the right side.
          if (cells.length >= 8) {
            const probe = [8, 9, 10, 7, 11];
            for (const idx of probe) {
              if (idx < cells.length) {
                const v = parseNum(cells[idx].textContent || '');
                if (v !== null) return v;
              }
            }
          }
          return null;
        }
        """
        try:
            val = page.evaluate(script, plant_name)
            if val is None:
                return None
            v = float(val)
            if v < 0:
                return None
            return v
        except Exception:
            return None

    def _extract_active_power_from_device_table(self, page) -> Optional[float]:
        script = """
        () => {
          const norm = (s) => (s || '').trim().toLowerCase();
          const parseNum = (txt) => {
            const rawMatch = String(txt || '').match(/-?\\d+(?:[.,]\\d+)*/);
            if (!rawMatch) return null;
            let raw = rawMatch[0];
            if (/^-?\\d{1,3}(?:,\\d{3})+(?:\\.\\d+)?$/.test(raw)) {
              raw = raw.replace(/,/g, '');
            } else if (/^-?\\d{1,3}(?:\\.\\d{3})+(?:,\\d+)?$/.test(raw)) {
              raw = raw.replace(/\\./g, '').replace(',', '.');
            } else {
              raw = raw.replace(',', '.');
            }
            const num = Number(raw);
            return Number.isFinite(num) ? num : null;
          };

          const tables = Array.from(document.querySelectorAll('table'));
          for (const table of tables) {
            const headers = Array.from(table.querySelectorAll('thead th, tr th')).map(th => norm(th.textContent || ''));
            if (!headers.length) continue;

            let powerIdx = -1;
            let typeIdx = -1;
            for (let i = 0; i < headers.length; i++) {
              const h = headers[i];
              if (powerIdx < 0 && h.includes('putere activÄƒ') && h.includes('(kw)')) powerIdx = i;
              if (powerIdx < 0 && h.includes('active power') && h.includes('(kw)')) powerIdx = i;
              if (typeIdx < 0 && (h.includes('tip dispozitiv') || h.includes('device type'))) typeIdx = i;
            }
            if (powerIdx < 0) continue;

            let total = 0;
            let found = 0;
            const rows = Array.from(table.querySelectorAll('tbody tr'));
            for (const row of rows) {
              const cells = Array.from(row.querySelectorAll('td'));
              if (cells.length <= powerIdx) continue;

              const rowText = norm(row.textContent || '');
              let isInverter = rowText.includes('sun2000') || rowText.includes('invertor') || rowText.includes('inverter');
              if (!isInverter && typeIdx >= 0 && cells.length > typeIdx) {
                const typeText = norm(cells[typeIdx].textContent || '');
                isInverter = typeText.includes('invertor') || typeText.includes('inverter');
              }
              if (!isInverter) continue;

              const value = parseNum(cells[powerIdx].textContent || '');
              if (value === null || value < 0) continue;
              total += value;
              found += 1;
            }
            if (found > 0 && total >= 0) return total;
          }
          return null;
        }
        """
        try:
            val = page.evaluate(script)
            if val is None:
                return None
            v = float(val)
            if v < 0:
                return None
            return v
        except Exception:
            return None

    def _click_plant_from_assets_table(self, page, plant_name: str) -> bool:
        target = plant_name.strip()
        if not target:
            return False
        target_re = re.compile(re.escape(target), re.I)
        is_horeco = "horeco" in target.lower()

        # Wait briefly for table rows to render on FusionSolar overview pages.
        try:
            page.locator("table tbody tr").first.wait_for(timeout=8_000)
        except PlaywrightTimeoutError:
            pass

        # Typical FusionSolar layout: 3rd column is "Plant Name".
        candidates = (
            page.locator("table tbody tr td:nth-child(3)").filter(has_text=target_re),
            page.locator("tbody tr td:nth-child(3)").filter(has_text=target_re),
            page.locator("table tbody tr").filter(has_text=target_re).locator("td:nth-child(3)"),
            page.locator("table tbody tr").filter(has_text=target_re),
        )
        for loc in candidates:
            try:
                if loc.count() <= 0:
                    continue
                loc.first.click(force=True)
                return True
            except Exception:
                continue

        # Horeco-specific rule from this tenant: select the last plant entry containing "horeco".
        if is_horeco:
            horeco_cells = (
                page.locator("table tbody tr td:nth-child(3)").filter(has_text=re.compile("horeco", re.I)),
                page.locator("tbody tr td:nth-child(3)").filter(has_text=re.compile("horeco", re.I)),
                page.locator("table tbody tr").filter(has_text=re.compile("horeco", re.I)).locator("td:nth-child(3)"),
            )
            for loc in horeco_cells:
                try:
                    count = loc.count()
                except Exception:
                    continue
                if count <= 0:
                    continue
                try:
                    loc.nth(count - 1).click(force=True)
                    return True
                except Exception:
                    continue

        # DOM fallback: click visible row/cell that contains the target text.
        script = """
        (name) => {
          const norm = (s) => (s || '').toLowerCase();
          const wanted = norm(name);
          const wantsHoreco = wanted.includes('horeco');
          const rows = Array.from(document.querySelectorAll('table tbody tr'))
            .filter(r => r && r.offsetParent !== null);
          if (wantsHoreco) {
            const matches = rows.filter(r => norm(r.textContent || '').includes('horeco'));
            if (matches.length) {
              const row = matches[matches.length - 1];
              const cells = row.querySelectorAll('td');
              if (cells && cells.length >= 3) {
                cells[2].click();
                return true;
              }
              row.click();
              return true;
            }
          }
          for (const row of rows) {
            const txt = norm(row.textContent || '');
            if (!txt.includes(wanted)) continue;
            const cells = row.querySelectorAll('td');
            if (cells && cells.length >= 3) {
              cells[2].click();
              return true;
            }
            row.click();
            return true;
          }
          return false;
        }
        """
        try:
            return bool(page.evaluate(script, target))
        except Exception:
            return False


def _extract_kw(text: str, label: str) -> Optional[float]:
    escaped = re.escape(label)
    number = r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:[.,][0-9]+)?)"
    patterns = (
        rf"(?is)\b{escaped}\b\s*[:\-]?\s*{number}\s*(kW|MW|W)\b",
        rf"(?is){number}\s*(kW|MW|W)\s*\b{escaped}\b(?!\s*[A-Za-z])",
    )

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                value = _parse_number(match.group(1))
                if value is None:
                    return None
                unit = match.group(2).lower()
                if unit == "mw":
                    return value * 1000.0
                if unit == "w":
                    return value / 1000.0
                return value
            except (TypeError, ValueError):
                return None
    return None


def _extract_active_power_kw(text: str) -> Optional[float]:
    number = r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:[.,][0-9]+)?)"
    localized_patterns = (
        rf"(?i)\bPutere\s*activa\b[^\S\r\n]*[:\-]?[^\S\r\n]*{number}[^\S\r\n]*(kW|MW|W)\b",
        rf"(?i)\bPutere\s*curenta\b[^\S\r\n]*[:\-]?[^\S\r\n]*{number}[^\S\r\n]*(kW|MW|W)\b",
        rf"(?i){number}[^\S\r\n]*(kW|MW|W)[^\S\r\n]*Putere\s*activa\b",
        rf"(?i){number}[^\S\r\n]*(kW|MW|W)[^\S\r\n]*Putere\s*curenta\b",
    )
    english_patterns = (
        rf"(?i)\bActive\s*power\b[^\S\r\n]*[:\-]?[^\S\r\n]*{number}[^\S\r\n]*(kW|MW|W)\b",
        rf"(?i)\bCurrent\s*Power\b[^\S\r\n]*[:\-]?[^\S\r\n]*{number}[^\S\r\n]*(kW|MW|W)\b",
        rf"(?i){number}[^\S\r\n]*(kW|MW|W)[^\S\r\n]*Active\s*power\b",
        rf"(?i){number}[^\S\r\n]*(kW|MW|W)[^\S\r\n]*Current\s*Power\b",
    )

    for block in _iter_local_text_blocks(text):
        localized_block = _fold_ascii(block)
        for pattern in localized_patterns:
            m = re.search(pattern, localized_block)
            if not m:
                continue
            value = _parse_decimal_comma_number(m.group(1))
            if value is None:
                return None
            return _to_kw_value(value, m.group(2).lower())
        for pattern in english_patterns:
            m = re.search(pattern, block)
            if not m:
                continue
            value = _parse_number(m.group(1))
            if value is None:
                return None
            return _to_kw_value(value, m.group(2).lower())
    return None


def _fold_ascii(text: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", str(text))
        if not unicodedata.combining(char)
    )


def _iter_local_text_blocks(text: str) -> list[str]:
    lines = [ln.strip() for ln in str(text).splitlines() if ln and ln.strip()]
    compact = re.sub(r"\s+", " ", str(text)).strip()
    if not lines:
        return [compact] if compact else []

    blocks: list[str] = [compact] if compact else []
    for i, line in enumerate(lines):
        blocks.append(line)
        if i + 1 < len(lines):
            blocks.append(f"{line} {lines[i + 1]}")
    return blocks


def _compact_excerpt(text: str, max_len: int = 1200) -> str:
    one_line = re.sub(r"\s+", " ", text).strip()
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 3] + "..."


def snapshot_to_dict(snapshot: PowerSnapshot) -> dict:
    return asdict(snapshot)


def _parse_number(raw: str) -> Optional[float]:
    cleaned = raw.strip()
    if re.fullmatch(r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?", cleaned):
        cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_decimal_comma_number(raw: str) -> Optional[float]:
    cleaned = raw.strip().replace(" ", "")
    if "." in cleaned and "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_inverter_nominal_power_kw(text: str) -> Optional[float]:
    localized_patterns = (
        r"(?is)\bPutere\s*nominala\s*invertor\b\s*[:\-]?\s*([0-9][0-9.,]*)\s*(kW|MW|W)\b",
    )
    localized_text = _fold_ascii(text)
    for pattern in localized_patterns:
        m = re.search(pattern, localized_text)
        if not m:
            continue
        value = _parse_decimal_comma_number(m.group(1))
        if value is None:
            return None
        return _to_kw_value(value, m.group(2).lower())

    english_patterns = (
        r"(?is)\bInverter\s*nominal\s*power\b\s*[:\-]?\s*([0-9][0-9.,]*)\s*(kW|MW|W)\b",
        r"(?is)\bRated\s*inverter\s*power\b\s*[:\-]?\s*([0-9][0-9.,]*)\s*(kW|MW|W)\b",
    )
    for pattern in english_patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        value = _parse_number(m.group(1))
        if value is None:
            return None
        return _to_kw_value(value, m.group(2).lower())
    return None


def _sanitize_active_power_kw(
    active_kw: Optional[float],
    nominal_kw: Optional[float],
    device_table_kw: Optional[float],
) -> Optional[float]:
    if active_kw is None:
        return None
    if nominal_kw is None or nominal_kw <= 0:
        return active_kw
    if active_kw <= nominal_kw * 1.05:
        return active_kw
    if device_table_kw is not None and device_table_kw <= nominal_kw * 1.05:
        return device_table_kw
    return None


def _normalize_ocr_kw_against_nominal(value_kw: Optional[float], nominal_kw: Optional[float]) -> Optional[float]:
    if value_kw is None or nominal_kw is None or nominal_kw <= 0:
        return value_kw
    if value_kw > nominal_kw * 10 and (value_kw / 1000.0) <= nominal_kw * 1.05:
        return value_kw / 1000.0
    return value_kw


def _should_replace_ocr_pv_kw(
    ocr_pv_kw: Optional[float],
    active_kw: Optional[float],
    nominal_kw: Optional[float],
) -> bool:
    if active_kw is None:
        return False
    if ocr_pv_kw is None:
        return True
    if nominal_kw is not None and nominal_kw > 0 and ocr_pv_kw > nominal_kw * 1.05:
        return True
    return _kw_values_materially_differ(ocr_pv_kw, active_kw)


def _select_overview_pv_kw(
    ocr_pv_kw: Optional[float],
    text: str,
    nominal_kw: Optional[float],
    device_table_kw: Optional[float],
) -> tuple[Optional[float], bool]:
    active_kw = _extract_active_power_kw(text)
    active_kw = _sanitize_active_power_kw(active_kw, nominal_kw, device_table_kw)
    if active_kw is not None and _should_replace_ocr_pv_kw(
        ocr_pv_kw,
        active_kw,
        nominal_kw,
    ):
        return active_kw, True
    return ocr_pv_kw, False


def _extract_flow_kw_ocr(page) -> tuple[Optional[float], Optional[float], Optional[float]]:
    cv2_mod, np_mod = _get_cv2_np()
    rapid_ocr = _get_rapidocr_class()
    if cv2_mod is None or np_mod is None or rapid_ocr is None:
        return None, None, None

    png = page.screenshot(full_page=True)
    arr = np_mod.frombuffer(png, dtype=np_mod.uint8)
    image = cv2_mod.imdecode(arr, cv2_mod.IMREAD_COLOR)
    if image is None:
        return None, None, None

    engine = rapid_ocr()
    result, _ = engine(image)
    if not result:
        return None, None, None

    entries = []
    for box, txt, score in result:
        if not txt:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        entries.append({"text": txt.strip(), "score": float(score), "cx": cx, "cy": cy})

    pv_kw = _value_for_label(entries, "PV")
    load_kw = _value_for_label(entries, "Load")
    grid_kw = _value_for_label(entries, "Grid")
    return pv_kw, load_kw, grid_kw


def _kw_values_materially_differ(left: float, right: float, rel_tol: float = 0.05, abs_tol_kw: float = 25.0) -> bool:
    return abs(left - right) > max(abs_tol_kw, max(abs(left), abs(right)) * rel_tol)


def _value_for_label(entries: list[dict], label: str) -> Optional[float]:
    label_key = label.lower()
    label_hits = [
        e for e in entries
        if e["text"].strip().lower() == label_key
        and e["cy"] > 250  # Skip menu/header labels.
    ]
    if not label_hits:
        return None

    label_hit = min(label_hits, key=lambda e: abs(e["cx"] - 520))
    lx = label_hit["cx"]
    ly = label_hit["cy"]

    candidates = []
    for e in entries:
        dx = abs(e["cx"] - lx)
        dy = e["cy"] - ly
        if dx > 160:
            continue
        # Account for both layouts:
        # - label above badge (value below), and
        # - label below badge (value above).
        if not (-170 <= dy <= 170):
            continue
        m = NUM_RE.search(e["text"])
        if not m:
            continue
        value = _parse_number(m.group(1))
        if value is None:
            continue
        unit = _unit_for_value_entry(entries, e)
        value_kw = _to_kw_value(value, unit)
        # Prefer values near the label axis and close vertical distance.
        score = (dx * 0.7) + abs(dy)
        candidates.append((score, value_kw))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _unit_for_value_entry(entries: list[dict], value_entry: dict) -> str:
    txt = str(value_entry.get("text") or "")
    m = re.search(r"(?i)\b(kW|MW|W)\b", txt)
    if m:
        return m.group(1).lower()

    vx = float(value_entry.get("cx") or 0.0)
    vy = float(value_entry.get("cy") or 0.0)
    unit_hits = []
    for e in entries:
        utxt = str(e.get("text") or "").strip().lower()
        if utxt not in {"kw", "mw", "w"}:
            continue
        dx = abs(float(e.get("cx") or 0.0) - vx)
        dy = abs(float(e.get("cy") or 0.0) - vy)
        if dx <= 120 and dy <= 90:
            unit_hits.append((dx + dy, utxt))

    if not unit_hits:
        return "kw"
    unit_hits.sort(key=lambda item: item[0])
    return unit_hits[0][1]


def _to_kw_value(value: float, unit: str) -> float:
    u = (unit or "kw").strip().lower()
    if u == "mw":
        return value * 1000.0
    if u == "w":
        return value / 1000.0
    return value

