from __future__ import annotations

import os
import re
import ctypes
import time
import tempfile
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json

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

from power_reading.scrapers.fusionsolar_scraper import PowerSnapshot
from PIL import Image, ImageGrab


P_PATTERNS = (
    re.compile(r"(?is)\bP\b(?!\s*F\b)\s*[:=]?\s*(-?[0-9]+(?:[.,][0-9]+)?)\s*(kW|W|MW)\b"),
    re.compile(r"(?is)\b(kW|W|MW)\b\s*(-?[0-9]+(?:[.,][0-9]+)?)\s*\bP\b(?!\s*F\b)"),
    re.compile(r"(?is)\bP\b(?!\s*F\b).{0,40}?(-?[0-9]+(?:[.,][0-9]+)?)\s*(kW|W|MW)\b"),
)
NUM_RE = re.compile(r"^-?[0-9][0-9\s.,']*$")


class AstroScraper:
    def __init__(
        self,
        target_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        http_username: Optional[str] = None,
        http_password: Optional[str] = None,
        user_data_dir: str = ".playwright_profile_astro",
        browser_timeout_ms: int = 45_000,
        headless: bool = False,
        post_login_wait_ms: int = 2_000,
        debug_pre_scrape_wait_ms: int = 0,
        debug_keep_open_ms: int = 0,
        debug_artifact_dir: Optional[str] = None,
        value_wait_attempts: int = 48,
        value_wait_sleep_ms: int = 1500,
        scada_panel_name: Optional[str] = None,
        scada_force: bool = False,
        force_fresh_profile: bool = False,
    ) -> None:
        self.target_url = target_url
        self.username = username
        self.password = password
        self.http_username = http_username
        self.http_password = http_password
        self.user_data_dir = Path(user_data_dir)
        self.browser_timeout_ms = browser_timeout_ms
        self.headless = headless
        self.post_login_wait_ms = max(0, int(post_login_wait_ms))
        self.debug_pre_scrape_wait_ms = max(0, int(debug_pre_scrape_wait_ms))
        self.debug_keep_open_ms = max(0, int(debug_keep_open_ms))
        self.debug_artifact_dir = Path(debug_artifact_dir) if debug_artifact_dir else None
        # Longer polling window for slow WinCC refresh cycles.
        self.value_wait_attempts = max(8, int(value_wait_attempts))
        self.value_wait_sleep_ms = max(500, int(value_wait_sleep_ms))
        self.scada_panel_name = (scada_panel_name or os.getenv("ASTRO_SCADA_PANEL_NAME") or "").strip() or None
        raw_force = str(os.getenv("ASTRO_SCADA_FORCE", "")).strip().lower()
        env_force = raw_force in {"1", "true", "yes", "on"}
        self.scada_force = bool(scada_force or env_force)
        self.force_fresh_profile = bool(force_fresh_profile)

    def scrape_once(self) -> PowerSnapshot:
        context_root = self.user_data_dir
        temp_profile = None
        if self.force_fresh_profile:
            temp_profile = tempfile.TemporaryDirectory(prefix=f"{self.user_data_dir.name}_", dir=str(Path.cwd()))
            context_root = Path(temp_profile.name)
        context_root.mkdir(parents=True, exist_ok=True)

        try:
            with sync_playwright() as p:
                context_kwargs = {
                "user_data_dir": str(context_root.resolve()),
                "headless": self.headless,
                "ignore_https_errors": True,
                }
                if self.headless:
                    context_kwargs["viewport"] = {"width": 1400, "height": 1000}
                else:
                    context_kwargs["no_viewport"] = True
                    context_kwargs["args"] = ["--start-maximized"]
                auth_user = self.http_username or self.username
                auth_pass = self.http_password or self.password
                if auth_user and auth_pass:
                    context_kwargs["http_credentials"] = {
                        "username": auth_user,
                        "password": auth_pass,
                    }
                context: BrowserContext = p.chromium.launch_persistent_context(
                    **context_kwargs,
                )
                try:
                    page = context.new_page()
                    page.set_default_timeout(self.browser_timeout_ms)
                    self._goto_resilient(page)
                    self._maybe_login(page)
                    self._bring_page_to_front(page)
                    if self.debug_pre_scrape_wait_ms > 0:
                        page.wait_for_timeout(self.debug_pre_scrape_wait_ms)
                    self._save_debug_artifacts(page, stage="post-login")

                    try:
                        page.wait_for_load_state("networkidle", timeout=12_000)
                    except PlaywrightTimeoutError:
                        pass

                    text, p_kw = self._wait_for_p_value(page)
                    self._save_debug_artifacts(page, stage="post-wait", text=text)
                    source = "astro-text-pattern" if p_kw is not None else "astro-unmatched"

                    # Optional SCADA panel parser (e.g., CEF DABACA -> Valori masurate P).
                    # Keeps existing behavior unchanged unless ASTRO_SCADA_PANEL_NAME is configured.
                    if self.scada_panel_name:
                        panel_kw = _extract_scada_panel_p_kw(text, self.scada_panel_name)
                        if panel_kw is not None and (self.scada_force or p_kw is None):
                            p_kw = panel_kw
                            source = f"astro-scada-panel:{self.scada_panel_name}"

                    if p_kw is None:
                        ocr_kw, ocr_text = _extract_p_kw_ocr(page)
                        if self.scada_panel_name and ocr_text:
                            panel_kw_ocr = _extract_scada_panel_p_kw(ocr_text, self.scada_panel_name)
                            if panel_kw_ocr is not None:
                                p_kw = panel_kw_ocr
                                source = f"astro-scada-panel-ocr:{self.scada_panel_name}"
                                text = f"{text} {ocr_text}".strip()
                        if p_kw is None and ocr_kw is not None:
                            p_kw = ocr_kw
                            if ocr_text:
                                text = f"{text} {ocr_text}".strip()
                            source = "astro-ocr-pattern"

                    result = PowerSnapshot(
                        pv_kw=p_kw,
                        load_kw=None,
                        grid_kw=None,
                        timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                        source=source,
                        raw_excerpt=_compact_excerpt(text),
                    )
                    if self.debug_keep_open_ms > 0:
                        self._bring_page_to_front(page)
                        page.wait_for_timeout(self.debug_keep_open_ms)
                    return result
                finally:
                    context.close()
        finally:
            if temp_profile is not None:
                temp_profile.cleanup()

    def _goto_resilient(self, page) -> None:
        attempts = (
            ("domcontentloaded", self.browser_timeout_ms),
            ("load", self.browser_timeout_ms + 20_000),
            ("commit", self.browser_timeout_ms),
        )
        urls = _navigation_candidates(self.target_url)
        last_exc: Exception | None = None
        tried: list[str] = []
        for url in urls:
            for wait_until, timeout_ms in attempts:
                try:
                    page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                    return
                except PlaywrightTimeoutError as exc:
                    last_exc = exc
                    tried.append(f"{url} [{wait_until}]")
                    # Keep trying less strict load states and alternative URL forms.
                    continue
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    tried.append(f"{url} [{wait_until}]")
                    continue
        if last_exc is not None:
            raise RuntimeError(f"Astro navigation failed after retries ({', '.join(tried[-6:])}): {last_exc}") from last_exc

    def _wait_for_p_value(self, page) -> tuple[str, Optional[float]]:
        last_text = ""
        for _ in range(self.value_wait_attempts):
            try:
                last_text = self._collect_page_text(page)
            except Exception:
                page.wait_for_timeout(1_000)
                continue

            p_kw = _extract_p_kw(last_text)
            if p_kw is not None:
                return last_text, p_kw
            page.wait_for_timeout(self.value_wait_sleep_ms)
        return last_text, None

    def _bring_page_to_front(self, page) -> None:
        if self.headless:
            return
        try:
            page.bring_to_front()
        except Exception:
            pass

    def _collect_page_text(self, page) -> str:
        chunks: list[str] = []
        try:
            body_text = page.locator("body").inner_text()
            if body_text:
                chunks.append(body_text)
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
                chunks.append(svg_text)
        except Exception:
            pass
        if not chunks:
            try:
                html = page.content()
                if html:
                    chunks.append(html)
            except Exception:
                pass
        return "\n".join(chunks)
        try:
            page.evaluate("() => window.focus()")
        except Exception:
            pass

    def _save_debug_artifacts(self, page, stage: str, text: str = "") -> None:
        if self.debug_artifact_dir is None:
            return
        try:
            self.debug_artifact_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
            stem = f"{ts}-{stage}"
            meta = {
                "stage": stage,
                "url": page.url,
                "title": page.title(),
            }
            (self.debug_artifact_dir / f"{stem}.json").write_text(
                json.dumps(meta, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            body_text = text
            if not body_text:
                try:
                    body_text = page.locator("body").inner_text()
                except Exception:
                    body_text = ""
            (self.debug_artifact_dir / f"{stem}.txt").write_text(body_text or "", encoding="utf-8")
            try:
                html = page.content()
            except Exception:
                html = ""
            (self.debug_artifact_dir / f"{stem}.html").write_text(html or "", encoding="utf-8")
            try:
                page.screenshot(path=str(self.debug_artifact_dir / f"{stem}.png"), full_page=True)
            except Exception:
                pass
        except Exception:
            pass

    def _maybe_login(self, page) -> None:
        if not (self.username and self.password):
            return

        if self._maybe_login_wincc_webux(page):
            return

        username_candidates = (
            page.locator("input[name='username']"),
            page.locator("input[id='username']"),
            page.locator("input[type='text']"),
        )
        password_candidates = (
            page.locator("input[name='password']"),
            page.locator("input[id='password']"),
            page.locator("input[type='password']"),
        )
        login_btn = (
            page.get_by_role("button", name=re.compile("login|sign in|connect", re.I)),
            page.locator("input[type='submit']"),
            page.locator("button"),
        )

        user_input = _first_present(username_candidates)
        pass_input = _first_present(password_candidates)
        if user_input is None or pass_input is None:
            return

        try:
            user_input.fill(self.username)
            pass_input.fill(self.password)
            btn = _first_present(login_btn)
            if btn is not None:
                btn.first.click()
            else:
                pass_input.press("Enter")
            if self.post_login_wait_ms > 0:
                page.wait_for_timeout(self.post_login_wait_ms)
        except Exception:
            return

    def _maybe_login_wincc_webux(self, page) -> bool:
        try:
            login_name = page.locator("[data-tif-id='@LoginName']")
            login_pw = page.locator("[data-tif-id='@LoginPW']")
            login_btn = page.locator("[data-tif-id='@ButtonLogin']")
            try:
                login_name.first.wait_for(state="visible", timeout=8_000)
                login_pw.first.wait_for(state="visible", timeout=8_000)
                login_btn.first.wait_for(state="visible", timeout=8_000)
            except Exception:
                return False
            page.wait_for_timeout(1500)
            if login_name.count() == 0 or login_pw.count() == 0 or login_btn.count() == 0:
                return False

            user_input = page.locator("#ID_InpInputUserName")
            pwd_input = page.locator("#ID_InpInputPwd")
            if user_input.count() == 0 or pwd_input.count() == 0:
                return False

            login_name.first.click(force=True)
            user_input.wait_for(state="visible", timeout=3_000)
            user_input.fill(self.username)

            login_pw.first.click(force=True)
            pwd_input.wait_for(state="visible", timeout=3_000)
            pwd_input.fill(self.password)

            login_btn.first.click(force=True)
            try:
                page.locator("[data-tif-id='MAIN']").first.wait_for(state="attached", timeout=max(self.post_login_wait_ms, 8_000))
            except Exception:
                if self.post_login_wait_ms > 0:
                    page.wait_for_timeout(self.post_login_wait_ms)
            return page.locator("[data-tif-id='MAIN']").count() > 0
        except Exception:
            return False

    def _type_into_wincc_overlay(self, page, value: str) -> bool:
        try:
            page.keyboard.press("Control+A")
        except Exception:
            pass
        try:
            page.keyboard.press("Backspace")
        except Exception:
            pass
        try:
            page.keyboard.type(value, delay=40)
            return True
        except Exception:
            return False

    def _click_center(self, locator) -> bool:
        try:
            box = locator.bounding_box()
            if not box:
                locator.click(force=True)
                return True
            locator.page.mouse.click(box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0)
            return True
        except Exception:
            try:
                locator.click(force=True)
                return True
            except Exception:
                return False


def _first_present(locators):
    for loc in locators:
        try:
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def _extract_p_kw(text: str) -> Optional[float]:
    wincc_summary_kw = _extract_wincc_summary_p_kw(text)
    if wincc_summary_kw is not None:
        return wincc_summary_kw

    for pattern in P_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if match.lastindex != 2:
            continue
        g1 = match.group(1)
        g2 = match.group(2)
        if _is_unit(g1):
            unit = g1.lower()
            value = _parse_number(g2)
        else:
            value = _parse_number(g1)
            unit = g2.lower()
        if value is None:
            continue
        return _to_kw(value, unit)

    # Fallback: pick nearest numeric token around standalone "P".
    tokens = [t for t in re.split(r"\s+", text) if t]
    for i, tok in enumerate(tokens):
        if tok.upper() != "P":
            continue
        if i + 1 < len(tokens) and tokens[i + 1].upper() == "F":
            continue
        for dist in (1, 2, 3, 4):
            for j in (i - dist, i + dist):
                if j < 0 or j >= len(tokens):
                    continue
                candidate = tokens[j].strip("[](){}:,;")
                if not NUM_RE.match(candidate):
                    continue
                value = _parse_number(candidate)
                if value is not None:
                    return value

    # Fallback for WinCC tables: parse values from "Active Power [kW]" rows and
    # prefer the largest positive value (typically plant-level point over inverter-level points).
    row_vals: list[float] = []
    for m in re.finditer(r"(?is)Active\s*Power\s*\[?\s*kW\s*\]?(.*?)(?:Reactive\s*Power|Voltage|Current|Temp|\Z)", text):
        segment = m.group(1) or ""
        for n in re.findall(r"-?[0-9][0-9\s.,']*", segment):
            v = _parse_number(n)
            if v is None:
                continue
            row_vals.append(v)
    if row_vals:
        positives = [v for v in row_vals if v > 0]
        if positives:
            return max(positives)

    # Another common block: "Active Power <value> kW" in ABB logger/footer.
    m2 = re.search(r"(?is)\bActive\s*Power\b\s*(-?[0-9][0-9\s.,']*)\s*(kW|W|MW)\b", text)
    if m2:
        value = _parse_number(m2.group(1))
        if value is not None:
            return _to_kw(value, m2.group(2).lower())
    return None


def _extract_wincc_summary_p_kw(text: str) -> Optional[float]:
    normalized = _normalize_power_text(text)
    preferred_patterns = (
        re.compile(
            r"(?is)INFORMATII\s*ANALIZORION\s*9200.*?\bP\b\s*\[\s*MW\s*\]\s*([+-]?[0-9]+(?:[.,][0-9]+)?)"
        ),
        re.compile(
            r"(?is)INFORMATII\s*INVERTOARE.*?\bP\b\s*\[\s*MW\s*\]\s*([+-]?[0-9]+(?:[.,][0-9]+)?)"
        ),
    )
    for pattern in preferred_patterns:
        match = pattern.search(normalized)
        if not match:
            continue
        value = _parse_number(match.group(1))
        if value is not None:
            return _to_kw(value, "mw")
    return None


def _is_unit(raw: str) -> bool:
    return raw.lower() in {"w", "kw", "mw"}


def _to_kw(value: float, unit: str) -> float:
    if unit == "mw":
        return value * 1000.0
    if unit == "w":
        return value / 1000.0
    return value


def _parse_number(raw: str) -> Optional[float]:
    cleaned = raw.strip().replace("\u00a0", " ").replace("'", "")
    # Handle grouped thousands with spaces, e.g. "1 195.29" or "1 195,29".
    cleaned = re.sub(r"\s+", "", cleaned)
    if cleaned.count(",") > 1 and cleaned.count(".") == 0:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned and "." in cleaned:
        # Assume comma is thousands separator when both are present.
        cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalize_power_text(text: str) -> str:
    return (
        text.replace("\u00a0", " ")
        .replace("Ã‚", "")
        .replace("Ãâ€ ", "phi")
        .replace("Ã†Å¾", "eta")
    )


def _compact_excerpt(text: str, max_len: int = 1200) -> str:
    one_line = re.sub(r"\s+", " ", text).strip()
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 3] + "..."


def _extract_p_kw_ocr(page) -> tuple[Optional[float], str]:
    cv2_mod, np_mod = _get_cv2_np()
    rapid_ocr = _get_rapidocr_class()
    if cv2_mod is None or np_mod is None or rapid_ocr is None:
        return None, ""
    try:
        png = page.screenshot(full_page=True)
        arr = np_mod.frombuffer(png, dtype=np_mod.uint8)
        image = cv2_mod.imdecode(arr, cv2_mod.IMREAD_COLOR)
        if image is None:
            return None, ""
        engine = rapid_ocr()
        result, _ = engine(image)
        if not result:
            return None, ""
        merged = _ocr_result_to_structured_text(result)
        return _extract_p_kw(merged), _compact_excerpt(merged)
    except Exception:
        return None, ""


def read_scada_open_window_once(panel_name: str, window_title_hint: Optional[str] = None) -> PowerSnapshot:
    merged_text, matched_title = _capture_open_scada_window_text(window_title_hint, panel_name)
    p_kw = _extract_scada_panel_p_kw(merged_text, panel_name)
    source = f"astro-scada-open-window:{matched_title}" if p_kw is not None else f"astro-scada-open-window-unmatched:{matched_title}"
    return PowerSnapshot(
        pv_kw=p_kw,
        load_kw=None,
        grid_kw=None,
        timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
        source=source,
        raw_excerpt=_compact_excerpt(merged_text),
    )


def read_scada_window_by_hwnd_once(panel_name: str, hwnd: int) -> PowerSnapshot:
    if os.name != "nt":
        raise RuntimeError("HWND-based SCADA capture is supported on Windows only.")
    cv2_mod, np_mod = _get_cv2_np()
    rapid_ocr = _get_rapidocr_class()
    if cv2_mod is None or np_mod is None or rapid_ocr is None:
        raise RuntimeError("OCR dependencies are missing (cv2/numpy/rapidocr).")

    image = _capture_hwnd_image(hwnd)
    if image is None:
        raise RuntimeError(f"Unable to capture window for hwnd: {hwnd}")
    engine = rapid_ocr()
    p_kw = _extract_scada_panel_p_kw_from_image(image, panel_name, engine)
    if p_kw is None:
        # Some SCADA surfaces render only when foregrounded. Retry once by
        # temporarily focusing target window, then restore previous foreground.
        allow_fg_retry = str(os.getenv("ASTRO_SCADA_FOREGROUND_RETRY", "true")).strip().lower() in {"1", "true", "yes", "on"}
        if allow_fg_retry:
            prev_hwnd = _get_foreground_hwnd()
            bbox = _window_rect_by_hwnd(hwnd)
            if bbox is not None:
                image2 = _capture_window_image(hwnd, bbox, focus_first=True)
                if image2 is not None:
                    p_kw = _extract_scada_panel_p_kw_from_image(image2, panel_name, engine)
            if prev_hwnd is not None and int(prev_hwnd) != int(hwnd):
                _focus_window(int(prev_hwnd))
    include_excerpt = str(os.getenv("ASTRO_SCADA_INCLUDE_EXCERPT", "false")).strip().lower() in {"1", "true", "yes", "on"}
    merged_text = ""
    if p_kw is None and include_excerpt:
        # Optional deep OCR pass for diagnostics only; disabled by default for speed.
        merged_text = _ocr_image_text(image, engine)
        if merged_text:
            p_kw = _extract_scada_panel_p_kw(merged_text, panel_name)
    source = f"astro-scada-hwnd:{hwnd}" if p_kw is not None else f"astro-scada-hwnd-unmatched:{hwnd}"
    return PowerSnapshot(
        pv_kw=p_kw,
        load_kw=None,
        grid_kw=None,
        timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
        source=source,
        raw_excerpt=_compact_excerpt(merged_text),
    )


def probe_scada_window_hwnd(panel_name: str, hwnd: int) -> dict:
    try:
        image = _capture_hwnd_image(hwnd)
        if image is None:
            raise RuntimeError(f"Unable to capture window for hwnd: {hwnd}")
        cv2_mod, np_mod = _get_cv2_np()
        rapid_ocr = _get_rapidocr_class()
        if cv2_mod is None or np_mod is None or rapid_ocr is None:
            raise RuntimeError("OCR dependencies are missing (cv2/numpy/rapidocr).")
        engine = rapid_ocr()
        p_kw_img = _extract_scada_panel_p_kw_from_image(image, panel_name, engine) if panel_name else None
        text = _ocr_image_text(image, engine)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "hwnd": int(hwnd),
            "error": str(exc),
            "text_len": 0,
            "has_panel": False,
            "p_kw": None,
            "excerpt": "",
        }
    p_kw = p_kw_img if panel_name else None
    if p_kw is None and panel_name:
        p_kw = _extract_scada_panel_p_kw(text, panel_name)
    return {
        "ok": True,
        "hwnd": int(hwnd),
        "text_len": len(text or ""),
        "has_panel": bool(p_kw is not None),
        "p_kw": p_kw,
        "excerpt": _compact_excerpt(text or "", max_len=600),
    }


def list_scada_window_candidates(window_title_hint: Optional[str] = None, limit: int = 25) -> list[dict]:
    title_hint = (window_title_hint or os.getenv("ASTRO_SCADA_WINDOW_TITLE") or "SCADA").strip().lower()
    rows = _enumerate_windows_for_capture(title_hint)
    out: list[dict] = []
    for hwnd, title, bbox in rows[: max(1, int(limit))]:
        left, top, right, bottom = bbox
        proc_name = _get_window_process_name(int(hwnd))
        class_name = _get_window_class_name(int(hwnd))
        out.append(
            {
                "hwnd": int(hwnd),
                "title": title or "",
                "process": proc_name or "",
                "class_name": class_name or "",
                "left": int(left),
                "top": int(top),
                "right": int(right),
                "bottom": int(bottom),
                "width": int(max(0, right - left)),
                "height": int(max(0, bottom - top)),
            }
        )
    return out


def _capture_open_scada_window_text(window_title_hint: Optional[str], panel_name: str) -> tuple[str, str]:
    if os.name != "nt":
        raise RuntimeError("Open-window SCADA capture is supported on Windows only.")
    cv2_mod, np_mod = _get_cv2_np()
    rapid_ocr = _get_rapidocr_class()
    if cv2_mod is None or np_mod is None or rapid_ocr is None:
        raise RuntimeError("OCR dependencies are missing (cv2/numpy/rapidocr).")

    title_hint = (window_title_hint or os.getenv("ASTRO_SCADA_WINDOW_TITLE") or "SCADA").strip().lower()
    engine = rapid_ocr()

    # First pass: explicitly find the requested SCADA window by title (including
    # common OCR/typing variant "cient" -> "client"), even if it is in background.
    targeted = _capture_target_window_by_title(title_hint, engine)
    if targeted is not None:
        text, matched_title = targeted
        panel_kw = _extract_scada_panel_p_kw(text, panel_name)
        if panel_kw is not None:
            return text, matched_title

    candidates = _enumerate_windows_for_capture(title_hint)
    best_text = ""
    best_title = ""
    # Scan a bounded number of windows with active capture per candidate.
    for hwnd, title, bbox in candidates[:40]:
        image = _capture_window_image(hwnd, bbox, focus_first=False)
        if image is None:
            continue
        text = _ocr_image_text(image, engine)
        if not text:
            continue
        if len(text) > len(best_text):
            best_text = text
            best_title = title
        panel_kw = _extract_scada_panel_p_kw(text, panel_name)
        if panel_kw is not None:
            return text, title

    # Final fallback: OCR full desktop.
    image = ImageGrab.grab(all_screens=True)
    text = _ocr_image_text(image, engine)
    if text:
        return text, f"full-screen-fallback:{title_hint}"
    if best_text:
        return best_text, best_title or f"window-scan:{title_hint}"
    return "", f"window-scan-empty:{title_hint}"


def _capture_hwnd_text(hwnd: int) -> str:
    image = _capture_hwnd_image(hwnd)
    if image is None:
        raise RuntimeError(f"Unable to capture window for hwnd: {hwnd}")
    cv2_mod, np_mod = _get_cv2_np()
    rapid_ocr = _get_rapidocr_class()
    if cv2_mod is None or np_mod is None or rapid_ocr is None:
        raise RuntimeError("OCR dependencies are missing (cv2/numpy/rapidocr).")
    engine = rapid_ocr()
    text = _ocr_image_text(image, engine)
    if text and len(text.strip()) >= 20:
        return text

    # Fallback for app surfaces that render only when foregrounded.
    allow_active = str(os.getenv("ASTRO_SCADA_ACTIVE_FALLBACK", "false")).strip().lower() in {"1", "true", "yes", "on"}
    if allow_active:
        bbox = _window_rect_by_hwnd(hwnd)
        image2 = _capture_window_image(hwnd, bbox, focus_first=True) if bbox is not None else None
        if image2 is not None:
            text2 = _ocr_image_text(image2, engine)
            if text2 and len(text2.strip()) >= 20:
                return text2
    return text


def _capture_hwnd_image(hwnd: int) -> Optional[Image.Image]:
    bbox = _window_rect_by_hwnd(hwnd)
    if bbox is None:
        return None
    image = _capture_window_image(hwnd, bbox, focus_first=False)
    if image is not None:
        return image
    # Active retry for surfaces that don't paint in background.
    allow_active = str(os.getenv("ASTRO_SCADA_ACTIVE_FALLBACK", "false")).strip().lower() in {"1", "true", "yes", "on"}
    if allow_active:
        return _capture_window_image(hwnd, bbox, focus_first=True)
    return None


def _capture_target_window_by_title(title_hint: str, engine) -> Optional[tuple[str, str]]:
    target_hwnd, target_title, target_bbox = _find_best_title_window(title_hint)
    if target_hwnd is None or target_bbox is None:
        return None
    image = _capture_window_image(target_hwnd, target_bbox, focus_first=False)
    if image is None:
        return None
    text = _ocr_image_text(image, engine)
    if not text:
        return None
    return text, target_title


def _find_best_title_window(title_hint: str) -> tuple[Optional[int], str, Optional[tuple[int, int, int, int]]]:
    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    normalized_hint = _norm_title(title_hint)
    hint_variants = {normalized_hint}
    if "cient" in normalized_hint:
        hint_variants.add(normalized_hint.replace("cient", "client"))
    if "client" in normalized_hint:
        hint_variants.add(normalized_hint.replace("client", "cient"))

    proc_hint_raw = (os.getenv("ASTRO_SCADA_PROCESS_HINTS") or "scada,starter,wincc,ivy").strip().lower()
    proc_hints = [h.strip() for h in proc_hint_raw.split(",") if h.strip()]

    exact_title = _norm_title(os.getenv("ASTRO_SCADA_EXACT_TITLE") or "")
    exact_proc = _norm_title(os.getenv("ASTRO_SCADA_EXACT_PROCESS") or "")
    matches: list[tuple[int, str, tuple[int, int, int, int], float, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum_windows(hwnd, _lparam):
        try:
            length = user32.GetWindowTextLengthW(hwnd)
            title = ""
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = (buf.value or "").strip()
            proc_name = _get_window_process_name(int(hwnd))
            class_name = _get_window_class_name(int(hwnd))
            label = " ".join(x for x in (title, proc_name, class_name) if x).strip()
            t_norm = _norm_title(label)
            if not t_norm:
                return True
            proc_norm = _norm_title(proc_name)
            title_norm = _norm_title(title)

            # Strict filters when explicitly configured.
            if exact_title and exact_title not in title_norm:
                return True
            if exact_proc and exact_proc not in proc_norm:
                return True

            score = 0.0
            for hv in hint_variants:
                if not hv:
                    continue
                if hv in t_norm or t_norm in hv:
                    score = max(score, 1.0)
                else:
                    score = max(score, SequenceMatcher(None, hv, t_norm).ratio())
            label_low = label.lower()
            if any(h in label_low for h in proc_hints):
                score = max(score, 0.72)
            if score < 0.34:
                return True

            rect = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            w = int(rect.right - rect.left)
            h = int(rect.bottom - rect.top)
            # Allow minimized/offscreen tool windows as candidates too.
            if w < 80 or h < 20:
                return True
            area = w * h
            display = title or proc_name or class_name or f"hwnd:{int(hwnd)}"
            matches.append((int(hwnd), display, (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)), score, area))
        except Exception:
            return True
        return True

    user32.EnumWindows(_enum_windows, 0)
    if not matches:
        return None, "", None
    matches.sort(key=lambda m: (m[3], m[4]), reverse=True)
    hwnd, title, bbox, _score, _area = matches[0]
    return hwnd, title, bbox


def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _enumerate_windows_for_capture(title_hint: str) -> list[tuple[int, str, tuple[int, int, int, int]]]:
    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    exact_title = _norm_title(os.getenv("ASTRO_SCADA_EXACT_TITLE") or "")
    exact_proc = _norm_title(os.getenv("ASTRO_SCADA_EXACT_PROCESS") or "")
    matches: list[tuple[int, str, tuple[int, int, int, int], int, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum_windows(hwnd, _lparam):
        try:
            length = user32.GetWindowTextLengthW(hwnd)
            title = ""
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()
            proc_name = _get_window_process_name(int(hwnd))
            class_name = _get_window_class_name(int(hwnd))
            proc_norm = _norm_title(proc_name)
            title_norm = _norm_title(title)
            if exact_title and exact_title not in title_norm:
                return True
            if exact_proc and exact_proc not in proc_norm:
                return True
            rect = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            w = int(rect.right - rect.left)
            h = int(rect.bottom - rect.top)
            # Keep broad to include app windows that report compact bounds when minimized.
            if w < 80 or h < 20:
                return True
            area = w * h
            t_low = " ".join(x for x in (title.lower(), proc_name.lower(), class_name.lower()) if x)
            if title_hint and title_hint in t_low:
                prefer = 3
            elif any(k in t_low for k in ("scada", "web client", "ivy grid", "dispecerat", "starter", "wincc")):
                prefer = 2
            elif title or proc_name or class_name:
                prefer = 1
            else:
                prefer = 0
            display = title or proc_name or class_name or f"hwnd:{int(hwnd)}"
            matches.append((int(hwnd), display, (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)), area, prefer))
        except Exception:
            return True
        return True

    user32.EnumWindows(_enum_windows, 0)
    if not matches:
        return []
    matches.sort(key=lambda item: (item[4], item[3]), reverse=True)
    return [(m[0], m[1], m[2]) for m in matches]


def _capture_window_image(hwnd: int, bbox: tuple[int, int, int, int], focus_first: bool = False) -> Optional[Image.Image]:
    if focus_first:
        _focus_window(hwnd)
        time.sleep(0.25)
    # Try fast non-blocking background capture first.
    image = _capture_window_via_bitblt(hwnd)
    if image is not None:
        return image
    # PrintWindow can hang for some app-rendered surfaces; keep it opt-in.
    allow_printwindow = str(os.getenv("ASTRO_SCADA_USE_PRINTWINDOW", "false")).strip().lower() in {"1", "true", "yes", "on"}
    if allow_printwindow:
        image = _capture_window_via_printwindow(hwnd)
        if image is not None:
            return image
    # Fallback to screen grab by window bounds.
    try:
        return ImageGrab.grab(bbox=bbox, all_screens=True)
    except Exception:
        return None


def _focus_window(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    try:
        # Only restore when minimized; do not change normal/maximized state.
        if user32.IsIconic(ctypes.c_void_p(hwnd)):
            user32.ShowWindow(ctypes.c_void_p(hwnd), SW_RESTORE)
        user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    except Exception:
        return


def _get_foreground_hwnd() -> Optional[int]:
    try:
        user32 = ctypes.windll.user32
        h = int(user32.GetForegroundWindow() or 0)
        return h if h > 0 else None
    except Exception:
        return None


def _capture_window_via_printwindow(hwnd: int) -> Optional[Image.Image]:
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", ctypes.c_ushort),
            ("biBitCount", ctypes.c_ushort),
            ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]

    PW_RENDERFULLCONTENT = 0x00000002
    BI_RGB = 0
    DIB_RGB_COLORS = 0

    rect = RECT()
    if not user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
        return None
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        return None

    hwnd_dc = user32.GetWindowDC(ctypes.c_void_p(hwnd))
    if not hwnd_dc:
        return None
    mfc_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    save_bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    if not mfc_dc or not save_bitmap:
        if save_bitmap:
            gdi32.DeleteObject(save_bitmap)
        if mfc_dc:
            gdi32.DeleteDC(mfc_dc)
        user32.ReleaseDC(ctypes.c_void_p(hwnd), hwnd_dc)
        return None

    gdi32.SelectObject(mfc_dc, save_bitmap)
    ok = user32.PrintWindow(ctypes.c_void_p(hwnd), mfc_dc, PW_RENDERFULLCONTENT)
    if not ok:
        ok = user32.PrintWindow(ctypes.c_void_p(hwnd), mfc_dc, 0)
    if not ok:
        gdi32.DeleteObject(save_bitmap)
        gdi32.DeleteDC(mfc_dc)
        user32.ReleaseDC(ctypes.c_void_p(hwnd), hwnd_dc)
        return None

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height  # top-down DIB
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    buf_len = width * height * 4
    buffer = ctypes.create_string_buffer(buf_len)
    bits_ok = gdi32.GetDIBits(
        mfc_dc,
        save_bitmap,
        0,
        height,
        buffer,
        ctypes.byref(bmi),
        DIB_RGB_COLORS,
    )

    gdi32.DeleteObject(save_bitmap)
    gdi32.DeleteDC(mfc_dc)
    user32.ReleaseDC(ctypes.c_void_p(hwnd), hwnd_dc)

    if bits_ok != height:
        return None

    try:
        return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
    except Exception:
        return None


def _window_rect_by_hwnd(hwnd: int) -> Optional[tuple[int, int, int, int]]:
    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    rect = RECT()
    ok = user32.GetWindowRect(ctypes.c_void_p(int(hwnd)), ctypes.byref(rect))
    if not ok:
        return None
    left, top, right, bottom = int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _capture_window_via_bitblt(hwnd: int) -> Optional[Image.Image]:
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", ctypes.c_ushort),
            ("biBitCount", ctypes.c_ushort),
            ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]

    BI_RGB = 0
    DIB_RGB_COLORS = 0
    SRCCOPY = 0x00CC0020

    rect = RECT()
    if not user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
        return None
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        return None

    hwnd_dc = user32.GetWindowDC(ctypes.c_void_p(hwnd))
    if not hwnd_dc:
        return None
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    if not mem_dc or not bmp:
        if bmp:
            gdi32.DeleteObject(bmp)
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(ctypes.c_void_p(hwnd), hwnd_dc)
        return None

    gdi32.SelectObject(mem_dc, bmp)
    ok = gdi32.BitBlt(mem_dc, 0, 0, width, height, hwnd_dc, 0, 0, SRCCOPY)
    if not ok:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(ctypes.c_void_p(hwnd), hwnd_dc)
        return None

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    buf_len = width * height * 4
    buffer = ctypes.create_string_buffer(buf_len)
    bits_ok = gdi32.GetDIBits(
        mem_dc,
        bmp,
        0,
        height,
        buffer,
        ctypes.byref(bmi),
        DIB_RGB_COLORS,
    )

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(ctypes.c_void_p(hwnd), hwnd_dc)

    if bits_ok != height:
        return None

    try:
        return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
    except Exception:
        return None


def _get_window_process_name(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    if pid.value == 0:
        return ""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not hproc:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(len(buf))
        ok = kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size))
        if not ok:
            return ""
        path = str(buf.value or "")
        if not path:
            return ""
        return Path(path).name
    except Exception:
        return ""
    finally:
        kernel32.CloseHandle(hproc)


def _get_window_class_name(hwnd: int) -> str:
    user32 = ctypes.windll.user32
    try:
        buf = ctypes.create_unicode_buffer(256)
        n = user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, 256)
        if n <= 0:
            return ""
        return str(buf.value or "")
    except Exception:
        return ""


def _ocr_image_text(image: Image.Image, engine) -> str:
    try:
        cv2_mod, np_mod = _get_cv2_np()
        if cv2_mod is None or np_mod is None:
            return ""
        arr = np_mod.array(image)
        if arr.size == 0:
            return ""

        variants = []
        # 1) Original RGB->BGR.
        variants.append(cv2_mod.cvtColor(arr, cv2_mod.COLOR_RGB2BGR))

        # 2) Upscaled + CLAHE on luminance for tiny SCADA fonts.
        up = cv2_mod.resize(arr, None, fx=2.0, fy=2.0, interpolation=cv2_mod.INTER_CUBIC)
        up_bgr = cv2_mod.cvtColor(up, cv2_mod.COLOR_RGB2BGR)
        lab = cv2_mod.cvtColor(up_bgr, cv2_mod.COLOR_BGR2LAB)
        l, a, b = cv2_mod.split(lab)
        clahe = cv2_mod.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l2 = clahe.apply(l)
        lab2 = cv2_mod.merge((l2, a, b))
        variants.append(cv2_mod.cvtColor(lab2, cv2_mod.COLOR_LAB2BGR))

        # 3) High-contrast thresholded grayscale.
        gray = cv2_mod.cvtColor(up_bgr, cv2_mod.COLOR_BGR2GRAY)
        thr = cv2_mod.adaptiveThreshold(
            gray,
            255,
            cv2_mod.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2_mod.THRESH_BINARY,
            31,
            7,
        )
        variants.append(cv2_mod.cvtColor(thr, cv2_mod.COLOR_GRAY2BGR))

        texts: list[str] = []
        for v in variants:
            result, _ = engine(v)
            if not result:
                continue
            txt = _ocr_result_to_structured_text(result)
            if txt:
                texts.append(txt)
        if not texts:
            return ""
        # Join unique passes to maximize pattern hit rate in noisy captures.
        uniq: list[str] = []
        seen = set()
        for t in texts:
            k = _norm_letters(t[:300])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(t)
        return "\n".join(uniq)
    except Exception:
        return ""


def _extract_scada_panel_p_kw_from_image(image: Image.Image, panel_name: str, engine) -> Optional[float]:
    if not panel_name:
        return None
    try:
        # Highest-priority path: locked ROI for CEF DABACA tile.
        kw_locked = _extract_dabaca_locked_roi_p_kw(image, panel_name, engine)
        if kw_locked is not None:
            return kw_locked
        name_norm = _norm_letters(panel_name)
        dabaca_roi_only = str(os.getenv("ASTRO_SCADA_DABACA_ROI_ONLY", "true")).strip().lower() in {"1", "true", "yes", "on"}
        if "dabaca" in name_norm and dabaca_roi_only:
            # Fast-fail path: do not run full-window OCR when locked ROI misses.
            return None
        variants = _ocr_image_variants(image)
        for v in variants:
            result, _ = engine(v)
            if not result:
                continue
            kw = _extract_panel_p_from_ocr_result(result, panel_name)
            if kw is not None:
                return kw
        # Fixed-layout fallback: bottom-right tile (CEF DABACA in this SCADA page).
        kw_br = _extract_bottom_right_panel_p_kw(image, engine)
        if kw_br is not None:
            return kw_br
    except Exception:
        return None
    return None


def _ocr_image_variants(image: Image.Image) -> list:
    cv2_mod, np_mod = _get_cv2_np()
    if cv2_mod is None or np_mod is None:
        return []
    arr = np_mod.array(image)
    if arr.size == 0:
        return []
    variants = []
    variants.append(cv2_mod.cvtColor(arr, cv2_mod.COLOR_RGB2BGR))
    up = cv2_mod.resize(arr, None, fx=2.0, fy=2.0, interpolation=cv2_mod.INTER_CUBIC)
    up_bgr = cv2_mod.cvtColor(up, cv2_mod.COLOR_RGB2BGR)
    variants.append(up_bgr)
    gray = cv2_mod.cvtColor(up_bgr, cv2_mod.COLOR_BGR2GRAY)
    thr = cv2_mod.adaptiveThreshold(gray, 255, cv2_mod.ADAPTIVE_THRESH_GAUSSIAN_C, cv2_mod.THRESH_BINARY, 31, 7)
    variants.append(cv2_mod.cvtColor(thr, cv2_mod.COLOR_GRAY2BGR))
    return variants


def _extract_panel_p_from_ocr_result(result, panel_name: str) -> Optional[float]:
    entries = []
    for item in result:
        try:
            if len(item) < 2:
                continue
            box = item[0]
            txt = str(item[1] or "").strip()
            if not txt or not box:
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            entries.append(
                {
                    "text": txt,
                    "norm": _norm_letters(txt),
                    "cx": sum(xs) / max(1, len(xs)),
                    "cy": sum(ys) / max(1, len(ys)),
                    "xmin": min(xs),
                    "xmax": max(xs),
                }
            )
        except Exception:
            continue
    if not entries:
        return None

    # Build OCR lines with geometry.
    lines: list[dict] = []
    y_tol = 16.0
    for e in sorted(entries, key=lambda x: (x["cy"], x["cx"])):
        placed = False
        for ln in lines:
            if abs(e["cy"] - ln["cy"]) <= y_tol:
                ln["items"].append(e)
                ln["cy"] = (ln["cy"] * 0.7) + (e["cy"] * 0.3)
                ln["xmin"] = min(ln["xmin"], e["xmin"])
                ln["xmax"] = max(ln["xmax"], e["xmax"])
                placed = True
                break
        if not placed:
            lines.append({"cy": e["cy"], "xmin": e["xmin"], "xmax": e["xmax"], "items": [e]})
    for ln in lines:
        ln["items"].sort(key=lambda it: it["cx"])
        ln["text"] = " ".join(it["text"] for it in ln["items"])
        ln["norm"] = _norm_letters(ln["text"])

    target = _norm_letters(panel_name)
    if not target:
        return None

    # Find best panel header line.
    best_i = -1
    best_score = 0.0
    for i, ln in enumerate(lines):
        n = ln["norm"]
        if "cef" not in n:
            continue
        score = SequenceMatcher(None, target, n).ratio()
        if target in n:
            score = max(score, 1.0)
        if score > best_score:
            best_score = score
            best_i = i
    if best_i < 0 or best_score < 0.45:
        return None

    # Scan lines under the selected panel header.
    for j in range(best_i, min(len(lines), best_i + 10)):
        txt = lines[j]["text"]
        m = re.search(r"(?is)\bValori\s*masurate\b.{0,120}?\bP\b\s*([0-9]+(?:[.,][0-9]+)?)\s*(MW|kW|W)\b", txt)
        if m:
            val = _parse_number(m.group(1))
            if val is not None:
                return _to_kw(val, m.group(2).lower())
        n = lines[j]["norm"]
        m2 = re.search(r"valorimasuratep([0-9]+(?:[.,][0-9]+)?)(mw|kw|w)", n)
        if m2:
            try:
                return _to_kw(float(m2.group(1).replace(",", ".")), m2.group(2).lower())
            except Exception:
                pass
    return None


def _extract_bottom_right_panel_p_kw(image: Image.Image, engine) -> Optional[float]:
    try:
        _, np_mod = _get_cv2_np()
        if np_mod is None:
            return None
        arr = np_mod.array(image)
        if arr.size == 0:
            return None
        h, w = arr.shape[:2]
        x1 = int(w * 0.52)
        x2 = int(w * 0.83)
        y1 = int(h * 0.72)
        y2 = int(h * 0.98)
        if x2 <= x1 or y2 <= y1:
            return None
        crop = arr[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        crop_img = Image.fromarray(crop)
        for v in _ocr_image_variants(crop_img):
            result, _ = engine(v)
            if not result:
                continue
            txt = _ocr_result_to_structured_text(result)
            if not txt:
                continue
            m = re.search(r"(?is)\bValori\s*masurate\b.{0,100}?\bP\b\s*([0-9]+(?:[.,][0-9]+)?)\s*(MW|kW|W)\b", txt)
            if m:
                val = _parse_number(m.group(1))
                if val is not None:
                    return _to_kw(val, m.group(2).lower())
            c = _norm_letters(txt)
            m2 = re.search(r"valorimasuratep([0-9]+(?:[.,][0-9]+)?)(mw|kw|w)", c)
            if m2:
                try:
                    return _to_kw(float(m2.group(1).replace(",", ".")), m2.group(2).lower())
                except Exception:
                    pass
    except Exception:
        return None
    return None


def _extract_dabaca_locked_roi_p_kw(image: Image.Image, panel_name: str, engine) -> Optional[float]:
    name_norm = _norm_letters(panel_name)
    if "dabaca" not in name_norm:
        return None
    enabled = str(os.getenv("ASTRO_SCADA_DABACA_LOCKED_ROI", "true")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return None
    try:
        # First, read the exact value cell for "Valori masurate P" in fullscreen layout.
        kw_value_cell = _extract_dabaca_value_cell_kw(image, engine)
        if kw_value_cell is not None:
            return kw_value_cell

        _, np_mod = _get_cv2_np()
        if np_mod is None:
            return None
        arr = np_mod.array(image)
        if arr.size == 0:
            return None
        h, w = arr.shape[:2]
        roi_raw = str(os.getenv("ASTRO_SCADA_DABACA_ROI") or "").strip()
        if roi_raw:
            parts = [p.strip() for p in roi_raw.split(",")]
            if len(parts) == 4:
                x1f, y1f, x2f, y2f = [float(x) for x in parts]
            else:
                x1f, y1f, x2f, y2f = (0.52, 0.76, 0.74, 0.94)
        else:
            x1f, y1f, x2f, y2f = (0.52, 0.76, 0.74, 0.94)
        x1 = int(max(0, min(w - 1, round(w * x1f))))
        x2 = int(max(0, min(w, round(w * x2f))))
        y1 = int(max(0, min(h - 1, round(h * y1f))))
        y2 = int(max(0, min(h, round(h * y2f))))
        if x2 <= x1 or y2 <= y1:
            return None

        crop = arr[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        crop_img = Image.fromarray(crop)
        for v in _ocr_image_variants(crop_img):
            result, _ = engine(v)
            if not result:
                continue
            txt = _ocr_result_to_structured_text(result)
            if not txt:
                continue
            kw = _extract_scada_masurate_p_kw(txt)
            if kw is not None:
                return kw
            compact = _norm_letters(txt)
            mi = compact.find("valorimasurate")
            if mi >= 0:
                tail = compact[mi : mi + 220]
                ii = tail.find("valoriimpuse")
                if ii > 0:
                    tail = tail[:ii]
                m = re.search(r"p(-?\d{1,4}(?:[.,]\d{1,3})?)(mw|kw|w)", tail)
                if m:
                    raw = m.group(1).replace(",", ".")
                    try:
                        return _to_kw(float(raw), m.group(2).lower())
                    except Exception:
                        pass
    except Exception:
        return None
    return None


def _extract_dabaca_value_cell_kw(image: Image.Image, engine) -> Optional[float]:
    try:
        _, np_mod = _get_cv2_np()
        if np_mod is None:
            return None
        arr = np_mod.array(image)
        if arr.size == 0:
            return None
        h, w = arr.shape[:2]
        roi_raw = str(os.getenv("ASTRO_SCADA_DABACA_VALUE_ROI") or "").strip()
        if roi_raw:
            parts = [p.strip() for p in roi_raw.split(",")]
            if len(parts) == 4:
                x1f, y1f, x2f, y2f = [float(x) for x in parts]
            else:
                x1f, y1f, x2f, y2f = (0.56, 0.885, 0.66, 0.94)
        else:
            x1f, y1f, x2f, y2f = (0.56, 0.885, 0.66, 0.94)

        x1 = int(max(0, min(w - 1, round(w * x1f))))
        x2 = int(max(0, min(w, round(w * x2f))))
        y1 = int(max(0, min(h - 1, round(h * y1f))))
        y2 = int(max(0, min(h, round(h * y2f))))
        if x2 <= x1 or y2 <= y1:
            return None

        crop = arr[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        crop_img = Image.fromarray(crop)
        for v in _ocr_image_variants(crop_img):
            result, _ = engine(v)
            if not result:
                continue
            txt = _ocr_result_to_structured_text(result)
            if not txt:
                continue
            # Prefer explicit unit when OCR catches it.
            m = re.search(r"(-?[0-9]+(?:[.,][0-9]+)?)\s*(MW|kW|W)\b", txt, flags=re.IGNORECASE)
            if m:
                val = _parse_number(m.group(1))
                if val is not None:
                    return _to_kw(val, m.group(2).lower())
            # Fallback: in this locked value-cell ROI, assume MW if unit is missing.
            m2 = re.search(r"(-?[0-9]+(?:[.,][0-9]+)?)", txt)
            if m2:
                val = _parse_number(m2.group(1))
                if val is not None:
                    return _to_kw(val, "mw")
    except Exception:
        return None
    return None


def _ocr_result_to_structured_text(result) -> str:
    entries: list[tuple[float, float, str]] = []
    for item in result:
        try:
            if len(item) < 2:
                continue
            box = item[0]
            txt = str(item[1] or "").strip()
            if not txt or not box:
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            cx = sum(xs) / max(1, len(xs))
            cy = sum(ys) / max(1, len(ys))
            entries.append((cy, cx, txt))
        except Exception:
            continue

    if not entries:
        return ""

    entries.sort(key=lambda e: (e[0], e[1]))
    lines: list[list[tuple[float, str]]] = []
    line_centers: list[float] = []
    y_tol = 18.0
    for cy, cx, txt in entries:
        placed = False
        for i, lc in enumerate(line_centers):
            if abs(cy - lc) <= y_tol:
                lines[i].append((cx, txt))
                line_centers[i] = (line_centers[i] * 0.7) + (cy * 0.3)
                placed = True
                break
        if not placed:
            lines.append([(cx, txt)])
            line_centers.append(cy)

    out_lines: list[str] = []
    for line in lines:
        line.sort(key=lambda p: p[0])
        out_lines.append(" ".join(t for _, t in line))
    return "\n".join(out_lines)


def _extract_scada_panel_p_kw(text: str, panel_name: str) -> Optional[float]:
    if not text or not panel_name:
        return None
    segment = _scada_panel_segment(text, panel_name, max_len=3500)
    if not segment:
        segment = _best_effort_panel_segment(text, panel_name)
    if not segment:
        return None
    panel_norm = _norm_letters(panel_name)
    seg_norm = _norm_letters(segment)
    # Guardrail: only parse values if this segment still looks like the requested panel.
    if panel_norm and panel_norm not in seg_norm:
        return None

    # Prefer strict extraction inside the local "Valori masurate" block.
    strict = _extract_scada_masurate_p_kw(segment)
    if strict is not None:
        return strict

    # OCR-compressed fallback, still constrained to masurate->impuse section.
    compact = _norm_letters(segment)
    mi = compact.find("valorimasurate")
    if mi >= 0:
        tail = compact[mi : mi + 320]
        ii = tail.find("valoriimpuse")
        if ii > 0:
            tail = tail[:ii]
        m3 = re.search(r"valorimasurate.*?p(-?\d{1,4}(?:[.,]\d{1,3})?)(mw|kw|w)", tail)
        if m3:
            raw = m3.group(1).replace(",", ".")
            try:
                return _to_kw(float(raw), m3.group(2).lower())
            except Exception:
                pass
    return None


def _extract_scada_masurate_p_kw(segment: str) -> Optional[float]:
    # 1) Bound by "Valori masurate" -> "Valori impuse" inside the same panel.
    m_block = re.search(r"(?is)\bValori\s*masurate\b(.{0,260}?)\bValori\s*impuse\b", segment)
    if m_block:
        block = m_block.group(1)
        m_val = re.search(r"(?is)\bP\b\s*(-?[0-9][0-9\s.,']*)\s*(kW|MW|W)\b", block)
        if m_val:
            val = _parse_number(m_val.group(1))
            if val is not None:
                return _to_kw(val, m_val.group(2).lower())

    # 2) If "impuse" is missing in OCR, keep a short window after masurate.
    m_short = re.search(r"(?is)\bValori\s*masurate\b(.{0,150})", segment)
    if m_short:
        block = m_short.group(1)
        m_val = re.search(r"(?is)\bP\b\s*(-?[0-9][0-9\s.,']*)\s*(kW|MW|W)\b", block)
        if m_val:
            val = _parse_number(m_val.group(1))
            if val is not None:
                return _to_kw(val, m_val.group(2).lower())
    return None


def _scada_panel_segment(text: str, panel_name: str, max_len: int = 3000) -> str:
    src = str(text or "")
    p = str(panel_name or "").strip()
    if not src or not p:
        return ""

    low = src.lower()
    p_low = p.lower()
    start = low.find(p_low)
    if start < 0:
        start = _fuzzy_panel_start(src, panel_name)
        if start < 0:
            start = _normalized_panel_start(src, panel_name)
        if start < 0:
            return ""

    end = min(len(src), start + max_len)
    tail = src[start + len(p) : end]
    next_panel = re.search(r"(?is)CEF\s*[A-Z0-9]", tail)
    if next_panel:
        end = start + len(p) + next_panel.start()
    return src[start:end]


def _fuzzy_panel_start(text: str, panel_name: str) -> int:
    target = _norm_letters(panel_name)
    if not target:
        return -1
    best_idx = -1
    best_score = 0.0
    for m in re.finditer(r"(?is)CEF.{0,80}", text):
        candidate = m.group(0)
        c_norm = _norm_letters(candidate)
        if not c_norm:
            continue
        score = SequenceMatcher(None, target, c_norm).ratio()
        if score > best_score:
            best_score = score
            best_idx = m.start()
    return best_idx if best_score >= 0.52 else -1


def _norm_letters(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _normalized_panel_start(text: str, panel_name: str) -> int:
    src = str(text or "")
    tgt = _norm_letters(panel_name)
    if not src or not tgt:
        return -1

    # Build normalized text and a map from normalized index to original index.
    norm_chars: list[str] = []
    index_map: list[int] = []
    for i, ch in enumerate(src):
        cl = ch.lower()
        if ("a" <= cl <= "z") or ("0" <= cl <= "9"):
            norm_chars.append(cl)
            index_map.append(i)
    norm_src = "".join(norm_chars)
    if not norm_src:
        return -1

    pos = norm_src.find(tgt)
    if pos >= 0 and pos < len(index_map):
        return index_map[pos]
    return -1


def _best_effort_panel_segment(text: str, panel_name: str) -> str:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    target = _norm_letters(panel_name)
    if not target:
        return ""

    best_idx = -1
    best_score = 0.0
    for i, ln in enumerate(lines):
        n = _norm_letters(ln)
        if not n:
            continue
        score = SequenceMatcher(None, target, n).ratio()
        if "cef" in n and score < 0.35:
            score += 0.08
        if "dab" in n:
            score += 0.06
        if score > best_score:
            best_score = score
            best_idx = i

    if best_idx < 0 or best_score < 0.34:
        return ""
    start = max(0, best_idx - 2)
    end = min(len(lines), best_idx + 12)
    return "\n".join(lines[start:end])


def _navigation_candidates(url: str) -> list[str]:
    raw = (url or "").strip()
    if not raw:
        return []

    out: list[str] = [raw]
    try:
        parts = urlsplit(raw)
    except Exception:
        return out

    if parts.scheme == "https":
        out.append(urlunsplit(("http", parts.netloc, parts.path, parts.query, parts.fragment)))
    elif parts.scheme == "http":
        out.append(urlunsplit(("https", parts.netloc, parts.path, parts.query, parts.fragment)))
    else:
        out.append(f"https://{raw.lstrip('/')}")
        out.append(f"http://{raw.lstrip('/')}")

    # Deduplicate while preserving order.
    uniq: list[str] = []
    seen = set()
    for candidate in out:
        if candidate in seen:
            continue
        seen.add(candidate)
        uniq.append(candidate)
    return uniq

