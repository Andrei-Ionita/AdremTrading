from __future__ import annotations

import csv
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional

from playwright.sync_api import BrowserContext, TimeoutError as PlaywrightTimeoutError, sync_playwright

from power_reading.scrapers.fusionsolar_scraper import PowerSnapshot


class ImperialScraper:
    def __init__(
        self,
        target_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        plant_name: Optional[str] = None,
        secondary_plant_name: Optional[str] = "Imperial 2",
        user_data_dir: str = ".playwright_profile_imperial",
        browser_timeout_ms: int = 60_000,
        headless: bool = False,
        min_visible_open_seconds: float = 10.0,
        force_relogin_each_run: bool = False,
        source_prefix: str = "imperial",
    ) -> None:
        self.target_url = target_url
        self.username = username
        self.password = password
        self.plant_name = plant_name
        self.secondary_plant_name = secondary_plant_name
        self.user_data_dir = Path(user_data_dir)
        self.browser_timeout_ms = browser_timeout_ms
        self.headless = headless
        self.min_visible_open_seconds = min_visible_open_seconds
        self.force_relogin_each_run = force_relogin_each_run
        self.source_prefix = source_prefix

    def scrape_once(self) -> PowerSnapshot:
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()

        with sync_playwright() as p:
            context: BrowserContext = p.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir.resolve()),
                headless=self.headless,
                ignore_https_errors=True,
                accept_downloads=True,
                viewport={"width": 1600, "height": 1000},
            )
            try:
                page = context.new_page()
                page.set_default_timeout(self.browser_timeout_ms)
                self._goto_with_fallbacks(page)
                if self.force_relogin_each_run:
                    self._force_relogin(page)
                else:
                    self._maybe_login(page)
                self._go_home(page)
                primary_plant = (self.plant_name or "PV Jucu").strip()

                p1_mw, p1_ts, csv1 = self._read_plant_power(
                    page,
                    _plant_aliases(primary_plant),
                    required=True,
                )
                if not self.secondary_plant_name:
                    used_visible_fallback = str(csv1).startswith("visible-power:")
                    source_suffix = "visible-fallback" if used_visible_fallback else "csv"
                    return PowerSnapshot(
                        pv_kw=p1_mw,
                        load_kw=None,
                        grid_kw=None,
                        timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                        source=(
                            f"{self.source_prefix}-{source_suffix}@{primary_plant}:"
                            f"{p1_ts or 'n/a'}"
                        ),
                        raw_excerpt=_compact_excerpt(f"{primary_plant} => {csv1}"),
                    )

                secondary_plant = self.secondary_plant_name.strip()
                p2_mw, p2_ts, csv2 = self._read_plant_power(
                    page,
                    _plant_aliases(secondary_plant),
                    required=True,
                )
                used_visible_fallback = str(csv1).startswith("visible-power:") or str(csv2).startswith("visible-power:")
                if p1_mw is None or p2_mw is None:
                    raise RuntimeError("Imperial requires valid power readings for both component plants.")
                if not used_visible_fallback:
                    aligned = _extract_latest_common_power_mw(csv1, csv2)
                    if aligned is None:
                        raise RuntimeError(
                            "Imperial CSV feeds have no common valid timestamp; refusing a mixed-quarter total."
                        )
                    p1_mw, p2_mw, common_ts = aligned
                    p1_ts = p2_ts = common_ts
                total_mw = p1_mw + p2_mw
                source_suffix = "visible-fallback" if used_visible_fallback else "csv"
                source = (
                    f"{self.source_prefix}-{source_suffix}@{primary_plant}:"
                    f"{p1_ts or 'n/a'}|{secondary_plant}:{p2_ts or 'n/a'}"
                    if total_mw is not None
                    else "imperial-unmatched"
                )
                raw_excerpt = _compact_excerpt(
                    f"{primary_plant} => {csv1}\n{secondary_plant} => {csv2}"
                )

                return PowerSnapshot(
                    # For Imperial, primary value is total asset power.
                    pv_kw=total_mw,
                    # Keep components available for UI visibility.
                    load_kw=p1_mw,
                    grid_kw=p2_mw,
                    timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                    source=source,
                    raw_excerpt=raw_excerpt,
                )
            finally:
                if not self.headless:
                    elapsed = time.monotonic() - started
                    remaining = self.min_visible_open_seconds - elapsed
                    if remaining > 0:
                        time.sleep(remaining)
                context.close()

    def _read_plant_power(
        self,
        page,
        target_plants: list[str],
        required: bool,
    ) -> tuple[Optional[float], Optional[str], str]:
        last_exc: Exception | None = None
        for attempt in range(4):
            # Session can drift to login between sequential plant reads; recover first.
            if _is_login_screen(page):
                self._maybe_login(page)
            # Reset to a stable entry point before every plant read.
            self._go_home(page)
            selected = self._open_target_plant(page, target_plants, required=required)
            if not selected and not required:
                return None, None, f"plant-not-found: {target_plants}"
            if required and not _is_plant_dashboard(page):
                last_exc = RuntimeError(f"Plant dashboard not open for {target_plants}")
                if attempt < 3:
                    self._goto_with_fallbacks(page)
                    self._maybe_login(page)
                    page.wait_for_timeout(2_000)
                    continue
                raise last_exc
            self._open_plant_performance_if_needed(page)
            self._set_1d_range(page)
            # Primary path: read visible on-screen power directly from dashboard.
            vis_mw = self._extract_visible_power_mw(page)
            if vis_mw is not None:
                ts = datetime.now(timezone.utc).isoformat()
                return vis_mw, ts, f"visible-power:{vis_mw}"
            try:
                self._ensure_csv_ready(page)
                csv_text, mw_value, ts = self._download_fresh_csv_snapshot(page)
                return mw_value, ts, csv_text
            except RuntimeError as exc:
                last_exc = exc
                if "CSV button not found" not in str(exc):
                    raise
                # Full navigation retry before failing.
                if attempt < 3:
                    self._goto_with_fallbacks(page)
                    self._maybe_login(page)
                    page.wait_for_timeout(2_500)
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Imperial plant read failed unexpectedly.")

    def _force_relogin(self, page) -> None:
        try:
            page.context.clear_cookies()
        except Exception:
            pass

        # Start from login and force fresh credential flow.
        login_urls = [
            self.target_url,
            "https://auroravision.net/ums/v1/loginPage",
            "https://www.auroravision.net/ums/v1/loginPage",
        ]
        for url in login_urls:
            if not url:
                continue
            try:
                page.goto(url, wait_until="domcontentloaded")
            except Exception:
                continue
            if _is_login_screen(page):
                break
        self._maybe_login(page)

    def _download_fresh_csv_snapshot(self, page) -> tuple[str, Optional[float], Optional[str]]:
        best_text = ""
        best_value = None
        best_ts = None
        for _ in range(6):
            csv_text = self._download_csv_text(page)
            mw_value, ts = _extract_latest_power_mw(csv_text)
            if _is_newer_ts(ts, best_ts):
                best_text, best_value, best_ts = csv_text, mw_value, ts
            if _is_recent_enough(best_ts, max_age_minutes=45):
                break
            page.wait_for_timeout(2_000)
        return best_text, best_value, best_ts

    def _goto_with_fallbacks(self, page) -> None:
        urls = [
            self.target_url,
            "https://auroravision.net/ums/v1/loginPage",
            "https://www.auroravision.net/ums/v1/loginPage",
        ]
        last_exc: Exception | None = None
        for url in urls:
            if not url:
                continue
            for wait_until in ("domcontentloaded", "load", "commit"):
                try:
                    page.goto(url, wait_until=wait_until)
                    page.wait_for_timeout(700)
                    return
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc).lower()
                    # Aurora frequently redirects between login/dashboard; treat this as success
                    # when browser already landed on Aurora domain.
                    if "interrupted by another navigation" in msg and "auroravision.net" in (page.url or "").lower():
                        return
                    last_exc = exc
                    continue
        if "auroravision.net" in (page.url or "").lower():
            return
        if last_exc is not None:
            raise RuntimeError(f"Imperial navigation failed for all known URLs: {last_exc}") from last_exc
        raise RuntimeError("Imperial navigation failed: no URL available.")

    def _download_csv_text(self, page) -> str:
        csv_btn = self._find_csv_button(page)
        if csv_btn is None:
            self._ensure_csv_ready(page)
            csv_btn = self._find_csv_button(page)

        with page.expect_download(timeout=25_000) as dl_info:
            clicked = False
            if csv_btn is not None:
                try:
                    csv_btn.first.click()
                    clicked = True
                except Exception:
                    clicked = False
            if not clicked:
                clicked = self._click_csv_via_js(page)
            if not clicked:
                raise RuntimeError(
                    f"CSV button not found on Imperial dashboard (url={page.url}, frames={len(page.frames)})."
                )
        download = dl_info.value

        with NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            tmp_path = Path(tmp.name)
        try:
            download.save_as(str(tmp_path))
            return tmp_path.read_text(encoding="utf-8", errors="replace")
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _maybe_login(self, page) -> None:
        if not _is_login_screen(page):
            return
        if not (self.username and self.password):
            raise RuntimeError("Imperial login page detected, but username/password are missing.")

        if _wait_until(lambda: _is_dashboard_screen(page), timeout_ms=8_000):
            return

        username_candidates = (
            page.get_by_label(re.compile(r"user\s*id|username|email", re.I)),
            page.locator("input[name='username']"),
            page.locator("input[id='username']"),
            page.locator("input[placeholder*='USER']"),
            page.locator("input[type='email']"),
            page.locator("input[type='text']"),
            page.locator("input:not([type='password'])"),
        )
        password_candidates = (
            page.get_by_label(re.compile(r"password", re.I)),
            page.locator("input[name='password']"),
            page.locator("input[id='password']"),
            page.locator("input[type='password']"),
        )

        user_input = _wait_for_first_present(username_candidates, timeout_ms=10_000)
        pass_input = _wait_for_first_present(password_candidates, timeout_ms=10_000)
        if user_input is None or pass_input is None:
            if _is_dashboard_screen(page):
                return
            raise RuntimeError("Imperial login fields not found.")

        _robust_fill(user_input, self.username)
        _robust_fill(pass_input, self.password)
        submit = page.get_by_role("button", name=re.compile(r"log-?in|login|sign in|aurora vision", re.I))
        if submit.count() == 0:
            submit = page.locator("button:has-text('Log-In')")
        if submit.count() > 0:
            submit.first.click()
        else:
            pass_input.first.press("Enter")

        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(2_500)
        if _is_login_screen(page):
            if _is_dashboard_screen(page):
                return
            raise RuntimeError("Imperial login did not succeed (still on login screen).")

    def _open_target_plant(self, page, target_plants: list[str], required: bool) -> bool:
        target_plants = [t.strip() for t in target_plants if t and t.strip()]
        if not target_plants:
            if required:
                raise RuntimeError("No target plant provided.")
            return False

        target_hint = target_plants[0]
        # Preferred path: on Home portfolio view, pick the plant directly from visible list.
        if self._select_plant_from_list(page, target_plants):
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(1_500)
            return True

        if (
            page.get_by_role("button", name=re.compile(r"\bCSV\b", re.I)).count() > 0
            and page.get_by_text(target_hint, exact=False).count() > 0
        ):
            return True

        plants_tab = page.get_by_role("link", name=re.compile(r"\bPlants\b", re.I))
        if plants_tab.count() == 0:
            plants_tab = page.get_by_role("button", name=re.compile(r"\bPlants\b", re.I))
        if plants_tab.count() == 0:
            plants_tab = page.locator("text=Plants")
        if plants_tab.count() > 0:
            plants_tab.first.click()
            page.wait_for_timeout(2_000)
            page.wait_for_timeout(5_000)

        # Then try list/table selection after explicit Plants navigation.
        if self._select_plant_from_list(page, target_plants):
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(1_500)
            return True

        # Some Aurora layouts expose plant switching via a combobox/dropdown
        # rather than a table row list.
        if self._select_plant_from_switcher(page, target_plants):
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(1_500)
            return True

        plant_hit = None
        for plant_name in target_plants:
            candidates = (
                page.get_by_role("link", name=re.compile(rf"^{re.escape(plant_name)}$", re.I)),
                page.get_by_role("cell", name=re.compile(rf"^{re.escape(plant_name)}$", re.I)),
                page.get_by_text(plant_name, exact=True),
                page.locator(f"text={plant_name}"),
                page.get_by_text(re.compile(re.escape(plant_name), re.I), exact=False),
            )
            for candidate in candidates:
                if _safe_count(candidate) > 0:
                    plant_hit = candidate
                    break
            if plant_hit is not None:
                break

        if plant_hit is None:
            # If list/switcher entries are hidden but we are already on plant dashboard,
            # continue with the current plant; otherwise signal not selected.
            return _is_plant_dashboard(page)

        plant_hit.first.click()

        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(2_000)
        return True

    def _select_plant_from_switcher(self, page, target_plants: list[str]) -> bool:
        # If current plant text already matches, no switch needed.
        for plant_name in target_plants:
            current_hits = (
                page.get_by_text(plant_name, exact=True),
                page.get_by_text(re.compile(rf"^\s*{re.escape(plant_name)}\s*$", re.I), exact=False),
            )
            for hit in current_hits:
                if _safe_count(hit) > 0 and _is_plant_dashboard(page):
                    return True

        switchers = (
            page.locator("div:has-text('PV Jucu')"),
            page.locator("div:has-text('Imperial 2')"),
            page.locator("div:has-text('Luna de Jos')"),
            page.get_by_role("combobox"),
            page.locator("[role='combobox']"),
            page.locator("select"),
            page.locator("[aria-haspopup='listbox']"),
            page.locator("[aria-expanded]"),
        )

        opened = False
        for sw in switchers:
            if _safe_count(sw) == 0:
                continue
            try:
                sw.first.click()
                page.wait_for_timeout(500)
                opened = True
                break
            except Exception:
                continue

        if not opened:
            return False

        for plant_name in target_plants:
            options = (
                page.get_by_role("option", name=re.compile(rf"^{re.escape(plant_name)}$", re.I)),
                page.get_by_role("menuitem", name=re.compile(rf"^{re.escape(plant_name)}$", re.I)),
                page.get_by_role("listitem", name=re.compile(rf"^{re.escape(plant_name)}$", re.I)),
                page.locator(f"li:has-text('{plant_name}')"),
                page.locator(f"div:has-text('{plant_name}')"),
                page.get_by_text(plant_name, exact=True),
                page.get_by_text(re.compile(re.escape(plant_name), re.I), exact=False),
            )
            for opt in options:
                if _safe_count(opt) == 0:
                    continue
                try:
                    opt.first.click()
                    page.wait_for_timeout(900)
                    return True
                except Exception:
                    continue
        return False

    def _select_plant_from_list(self, page, target_plants: list[str]) -> bool:
        for plant_name in target_plants:
            # Preferred: click the plant name cell itself.
            name_targets = (
                page.locator(f"td:text-is('{plant_name}')"),
                page.get_by_role("cell", name=re.compile(rf"^{re.escape(plant_name)}$", re.I)),
                page.get_by_text(plant_name, exact=True),
            )
            for nt in name_targets:
                if _safe_count(nt) == 0:
                    continue
                try:
                    nt.first.click()
                    page.wait_for_timeout(800)
                    if _wait_until(lambda: _is_plant_dashboard(page), timeout_ms=8_000):
                        return True
                except Exception:
                    continue

            # Fallback: click "Click to configure" in same row.
            row_scope = page.locator(f"tr:has-text('{plant_name}')")
            if _safe_count(row_scope) > 0:
                cfg = row_scope.first.get_by_role("link", name=re.compile(r"click to configure", re.I))
                if _safe_count(cfg) > 0:
                    try:
                        cfg.first.click()
                        page.wait_for_timeout(800)
                        if _wait_until(lambda: _is_plant_dashboard(page), timeout_ms=8_000):
                            return True
                    except Exception:
                        pass

            # Fallback: direct row/cell selection.
            fallbacks = (
                page.get_by_role("cell", name=re.compile(rf"^{re.escape(plant_name)}$", re.I)),
                page.locator(f"tr:has(td:text-is('{plant_name}'))"),
                page.locator(f"td:text-is('{plant_name}')"),
                page.get_by_role("row", name=re.compile(re.escape(plant_name), re.I)),
                page.get_by_role("link", name=re.compile(rf"^{re.escape(plant_name)}$", re.I)),
                page.get_by_text(plant_name, exact=True),
                page.get_by_text(re.compile(re.escape(plant_name), re.I), exact=False),
            )
            for target in fallbacks:
                if _safe_count(target) == 0:
                    continue
                try:
                    target.first.click()
                    page.wait_for_timeout(800)
                    if _wait_until(lambda: _is_plant_dashboard(page), timeout_ms=8_000):
                        return True
                except Exception:
                    continue
        return False

    def _go_home(self, page) -> None:
        home = page.get_by_role("link", name=re.compile(r"^\s*Home\s*$", re.I))
        if home.count() == 0:
            home = page.get_by_role("button", name=re.compile(r"^\s*Home\s*$", re.I))
        if home.count() == 0:
            home = page.locator("text=Home")
        if home.count() > 0:
            home.first.click()
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(1_500)

    def _open_plant_performance_if_needed(self, page) -> None:
        if page.get_by_role("button", name=re.compile(r"\bCSV\b", re.I)).count() > 0:
            return
        tab = page.get_by_role("tab", name=re.compile(r"Plant Performance", re.I))
        if tab.count() == 0:
            tab = page.get_by_text("Plant Performance", exact=False)
        if tab.count() > 0:
            tab.first.click()
            page.wait_for_timeout(1_500)

    def _set_1d_range(self, page) -> None:
        one_day = page.get_by_role("button", name=re.compile(r"^\s*1D\s*$", re.I))
        if one_day.count() == 0:
            one_day = page.get_by_role("tab", name=re.compile(r"^\s*1D\s*$", re.I))
        if one_day.count() == 0:
            one_day = page.get_by_text("1D", exact=True)
        if one_day.count() > 0:
            one_day.first.click()
            page.wait_for_timeout(1_500)

    def _ensure_csv_ready(self, page) -> None:
        for _ in range(12):
            if self._find_csv_button(page) is not None:
                return

            self._open_plant_performance_if_needed(page)
            self._set_1d_range(page)
            # If data is embedded in iframe-based dashboard variants, make a best effort
            # to focus/activate frame content before searching controls again.
            for fr in page.frames:
                if fr == page.main_frame:
                    continue
                try:
                    fr.locator("body").first.click(timeout=300)
                except Exception:
                    pass
            # Some Aurora layouts reveal export actions after opening a kebab/options menu.
            opts = (
                page.get_by_role("button", name=re.compile(r"more|options|export", re.I)),
                page.locator("button[aria-label*='more' i]"),
                page.locator("button[aria-label*='option' i]"),
                page.locator("button:has(svg)"),
            )
            for opt in opts:
                if _safe_count(opt) <= 0:
                    continue
                try:
                    opt.first.click()
                    page.wait_for_timeout(350)
                except Exception:
                    continue
            try:
                page.mouse.wheel(0, 1200)
            except Exception:
                pass
            page.wait_for_timeout(900)
        raise RuntimeError("CSV button not found on Imperial dashboard.")

    def _find_csv_button(self, page):
        contexts = [page, *[fr for fr in page.frames if fr != page.main_frame]]
        for ctx in contexts:
            candidates = (
                ctx.get_by_role("button", name=re.compile(r"^\s*CSV\s*$", re.I)),
                ctx.get_by_role("button", name=re.compile(r"\bCSV\b", re.I)),
                ctx.get_by_role("button", name=re.compile(r"download.*csv|export.*csv|csv.*download", re.I)),
                ctx.get_by_role("menuitem", name=re.compile(r"\bCSV\b|download|export", re.I)),
                ctx.locator("button:has-text('CSV')"),
                ctx.locator("a:has-text('CSV')"),
                ctx.locator("text=Download CSV"),
                ctx.locator("text=Export CSV"),
                ctx.locator("text=CSV"),
            )
            for loc in candidates:
                if _safe_count(loc) > 0:
                    return loc
        return None

    def _click_csv_via_js(self, page) -> bool:
        script = """
() => {
  const isVisible = (el) => {
    if (!el) return false;
    const st = window.getComputedStyle(el);
    if (!st) return false;
    if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity || 1) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const candidates = Array.from(document.querySelectorAll("button, a, [role='button'], [role='menuitem'], span, div"))
    .filter(el => {
      const t = (el.innerText || el.textContent || '').trim();
      if (!t) return false;
      const u = t.toUpperCase();
      return (u === 'CSV' || u.includes('EXPORT CSV') || u.includes('DOWNLOAD CSV')) && isVisible(el);
    });
  if (!candidates.length) return false;
  candidates.sort((a, b) => {
    const ar = a.getBoundingClientRect();
    const br = b.getBoundingClientRect();
    if (Math.abs(ar.top - br.top) > 2) return ar.top - br.top;
    return br.right - ar.right;
  });
  candidates[0].click();
  return true;
}
"""
        try:
            if bool(page.evaluate(script)):
                return True
        except Exception:
            pass

        for fr in page.frames:
            if fr == page.main_frame:
                continue
            try:
                if bool(fr.evaluate(script)):
                    return True
            except Exception:
                continue
        return False

    def _extract_visible_power_mw(self, page) -> Optional[float]:
        try:
            text = page.locator("body").inner_text()
        except Exception:
            return None

        patterns = (
            r"(?is)\bActive\s*Power\b[^0-9-]{0,30}(-?[0-9][0-9\s,.'']*)\s*(MW|kW|W)\b",
            r"(?is)\bCurrent\s*Power\b[^0-9-]{0,30}(-?[0-9][0-9\s,.'']*)\s*(MW|kW|W)\b",
            r"(?is)\bGenerated\s*Power\b[^0-9-]{0,30}(-?[0-9][0-9\s,.'']*)\s*(MW|kW|W)\b",
            r"(?is)\bPower\b[^0-9-]{0,30}(-?[0-9][0-9\s,.'']*)\s*(MW|kW|W)\b",
        )
        for pat in patterns:
            m = re.search(pat, text)
            if not m:
                continue
            value = _parse_number(m.group(1))
            if value is None:
                continue
            unit = (m.group(2) or "").lower()
            if unit == "mw":
                return value
            if unit == "kw":
                return value / 1000.0
            if unit == "w":
                return value / 1_000_000.0
        return None


def _first_present(locators):
    for loc in locators:
        try:
            if _safe_count(loc) > 0:
                return loc
        except Exception:
            continue
    return None


def _is_login_screen(page) -> bool:
    url_flag = "login" in (page.url or "").lower()
    has_user = _safe_count(page.get_by_text("USER ID", exact=False)) > 0
    has_pass = _safe_count(page.get_by_text("PASSWORD", exact=False)) > 0
    return url_flag or (has_user and has_pass)


def _is_dashboard_screen(page) -> bool:
    has_home = _safe_count(page.get_by_text("Home", exact=False)) > 0
    has_plants = _safe_count(page.get_by_text("Plants", exact=False)) > 0
    has_profile = _safe_count(page.get_by_text("PV Jucu-Luna", exact=False)) > 0
    return has_home or has_plants or has_profile


def _is_plant_dashboard(page) -> bool:
    # Portfolio/home screens also show "Produced Energy", so require plant-view cues.
    has_plant_perf = _safe_count(page.get_by_text("Plant Performance", exact=False)) > 0
    has_device_perf = _safe_count(page.get_by_text("Device Performance", exact=False)) > 0
    has_time_tabs = _safe_count(page.get_by_text(re.compile(r"^\s*1D\s*$", re.I), exact=False)) > 0
    has_csv = (
        _safe_count(page.get_by_role("button", name=re.compile(r"\bCSV\b", re.I))) > 0
        or _safe_count(page.locator("text=CSV")) > 0
    )
    return (has_plant_perf and has_device_perf) or has_time_tabs or has_csv


def _wait_until(predicate, timeout_ms: int = 8_000, poll_ms: int = 250) -> bool:
    start = time.monotonic()
    timeout_s = timeout_ms / 1000.0
    poll_s = poll_ms / 1000.0
    while (time.monotonic() - start) < timeout_s:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(poll_s)
    return False


def _wait_for_first_present(locators, timeout_ms: int = 8_000, poll_ms: int = 250):
    hit = _first_present(locators)
    if hit is not None:
        return hit
    start = time.monotonic()
    timeout_s = timeout_ms / 1000.0
    poll_s = poll_ms / 1000.0
    while (time.monotonic() - start) < timeout_s:
        hit = _first_present(locators)
        if hit is not None:
            return hit
        time.sleep(poll_s)
    return None


def _robust_fill(locator, value: str) -> None:
    loc = locator.first
    try:
        loc.click()
    except Exception:
        pass
    try:
        loc.fill(value)
        return
    except Exception:
        pass
    try:
        loc.press("Control+A")
        loc.press("Delete")
    except Exception:
        pass
    loc.type(value, delay=20)


def _safe_count(locator, retries: int = 6, delay_s: float = 0.2) -> int:
    for _ in range(retries):
        try:
            return locator.count()
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "execution context was destroyed" in msg or "most likely because of a navigation" in msg:
                time.sleep(delay_s)
                continue
            return 0
    return 0


def _plant_aliases(name: str) -> list[str]:
    base = (name or "").strip()
    aliases = [base]
    normalized = base.lower().replace(" ", "")
    if normalized in {"imperial2", "imperial-2"}:
        aliases.extend(
            [
                "Imperial2",
                "Imperial 2",
                "PV Luna de Jos",
                "Luna de Jos",
            ]
        )
    if normalized in {"pvjucu", "jucu"}:
        aliases.extend(["PV Jucu", "Jucu"])
    # preserve order, remove duplicates
    seen = set()
    out = []
    for item in aliases:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _extract_latest_power_mw(csv_text: str) -> tuple[Optional[float], Optional[str]]:
    series = _extract_power_series_mw(csv_text)
    if not series:
        return None, None

    latest_ts = max(series)
    power_mw, latest_ts_raw = series[latest_ts]
    return power_mw, latest_ts_raw


def _extract_latest_common_power_mw(
    primary_csv: str,
    secondary_csv: str,
) -> Optional[tuple[float, float, str]]:
    primary = _extract_power_series_mw(primary_csv)
    secondary = _extract_power_series_mw(secondary_csv)
    common_timestamps = primary.keys() & secondary.keys()
    if not common_timestamps:
        return None

    latest_common = max(common_timestamps)
    primary_mw, primary_ts_raw = primary[latest_common]
    secondary_mw, _ = secondary[latest_common]
    return primary_mw, secondary_mw, primary_ts_raw


def _extract_power_series_mw(csv_text: str) -> dict[datetime, tuple[float, str]]:
    reader = csv.reader(csv_text.splitlines())
    rows = [row for row in reader if row]
    if not rows:
        return {}

    header_idx = None
    for i, row in enumerate(rows):
        if row and row[0].strip().lower() == "timestamp":
            header_idx = i
            break
    if header_idx is None:
        return {}

    data_rows = rows[header_idx + 1 :]
    parsed: dict[datetime, tuple[float, str]] = {}
    for row in data_rows:
        if len(row) < 2:
            continue
        ts_raw = row[0].strip()
        energy_raw = row[1].strip()
        if not ts_raw or energy_raw in {"--", ""}:
            continue

        ts = _parse_ts(ts_raw)
        energy_kwh = _parse_number(energy_raw)
        if ts is None or energy_kwh is None:
            continue
        # In this tenant export, values align with chart tooltip "Generated Power: ... W".
        parsed[ts] = (energy_kwh / 1_000_000.0, ts_raw)
    return parsed


def _infer_interval_hours(timestamps: list[datetime]) -> float:
    if len(timestamps) < 2:
        return 0.25
    deltas = []
    for i in range(1, len(timestamps)):
        delta_h = (timestamps[i] - timestamps[i - 1]).total_seconds() / 3600.0
        if delta_h > 0:
            deltas.append(delta_h)
    if not deltas:
        return 0.25
    # Most feeds here are 15-min; use median-like robust pick.
    deltas.sort()
    return deltas[len(deltas) // 2]


def _parse_ts(raw: str) -> Optional[datetime]:
    txt = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(txt)
    except ValueError:
        return None


def _is_newer_ts(left_raw: Optional[str], right_raw: Optional[str]) -> bool:
    left = _parse_ts(left_raw) if left_raw else None
    right = _parse_ts(right_raw) if right_raw else None
    if left is None:
        return False
    if right is None:
        return True
    return left > right


def _is_recent_enough(ts_raw: Optional[str], max_age_minutes: int = 45) -> bool:
    ts = _parse_ts(ts_raw) if ts_raw else None
    if ts is None:
        return False
    now_utc = datetime.now(timezone.utc)
    age_minutes = (now_utc - ts).total_seconds() / 60.0
    return age_minutes <= max_age_minutes


def _parse_number(raw: str) -> Optional[float]:
    cleaned = raw.strip().replace(" ", "").replace("'", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _compact_excerpt(text: str, max_len: int = 1200) -> str:
    one_line = re.sub(r"\s+", " ", text).strip()
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 3] + "..."

