from __future__ import annotations

import re
import json
import base64
import ssl
import uuid
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, sync_playwright

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

from power_reading.scrapers.astro_scraper import _compact_excerpt, _first_present, _ocr_result_to_structured_text
from power_reading.scrapers.fusionsolar_scraper import PowerSnapshot


MEASURED_PATTERNS = (
    re.compile(r"(?is)\bMeasured\s*value\s*\[\s*kW\s*\]\s*([0-9][0-9\s.,']*)"),
    re.compile(r"(?is)\bMeasured\s*value\b\s*([0-9][0-9\s.,']*)\s*kW\b"),
)


class SNKScraper:
    def __init__(
        self,
        target_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        user_data_dir: str = ".playwright_profile_snk",
        browser_timeout_ms: int = 45_000,
        headless: bool = True,
        post_login_wait_ms: int = 2_000,
        force_fresh_profile: bool = True,
        value_wait_attempts: int = 60,
        value_wait_sleep_ms: int = 2_000,
        session_attempts: int = 3,
        debug_artifact_dir: Optional[str] = None,
        window_mode: str = "visible",
    ) -> None:
        self.target_url = target_url
        self.username = username
        self.password = password
        self.user_data_dir = Path(user_data_dir)
        self.browser_timeout_ms = browser_timeout_ms
        self.headless = bool(headless)
        self.post_login_wait_ms = max(0, int(post_login_wait_ms))
        self.force_fresh_profile = bool(force_fresh_profile)
        self.value_wait_attempts = max(1, int(value_wait_attempts))
        self.value_wait_sleep_ms = max(500, int(value_wait_sleep_ms))
        self.session_attempts = max(1, int(session_attempts))
        self.debug_artifact_dir = Path(debug_artifact_dir) if debug_artifact_dir else None
        self.window_mode = (window_mode or "visible").strip().lower()
        self._debug_events: list[str] = []

    def scrape_once(self) -> PowerSnapshot:
        self._trace("scrape:start")
        api_snapshot = self._scrape_once_api()
        if api_snapshot.pv_kw is not None:
            self._trace(f"api:success pv_kw={api_snapshot.pv_kw}")
            return api_snapshot
        self._trace("api:unmatched")
        last_snapshot: Optional[PowerSnapshot] = None
        for attempt in range(1, self.session_attempts + 1):
            self._trace(f"session:{attempt}:start")
            snapshot = self._scrape_once_session(attempt)
            if snapshot.pv_kw is not None:
                self._trace(f"session:{attempt}:success pv_kw={snapshot.pv_kw}")
                return snapshot
            last_snapshot = snapshot
            self._trace(f"session:{attempt}:unmatched")
        return last_snapshot or PowerSnapshot(
            pv_kw=None,
            load_kw=None,
            grid_kw=None,
            timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
            source="snk-unmatched",
            raw_excerpt="",
        )

    def _scrape_once_api(self) -> PowerSnapshot:
        try:
            token = self._snk_api_access_token()
            session_id = self._snk_api_create_session(token)
            values = self._snk_api_read_values(
                token,
                session_id,
                [
                    "iPcu",
                    "udtPcuControl.Data[1].Debug.Ctrl.PCtrl.PID.lrActVal",
                    "udtPcuControl.Config[1].lrActPwrNom",
                ],
            )
            pcu = int(float(values.get("Arp.Plc.Eclr/iPcu", 1) or 1))
            if pcu != 1:
                values = self._snk_api_read_values(
                    token,
                    session_id,
                    [
                        "iPcu",
                        f"udtPcuControl.Data[{pcu}].Debug.Ctrl.PCtrl.PID.lrActVal",
                        f"udtPcuControl.Config[{pcu}].lrActPwrNom",
                    ],
                )
            act_val = _first_float_value(values, ".Debug.Ctrl.PCtrl.PID.lrActVal")
            nominal = _first_float_value(values, ".lrActPwrNom")
            if act_val is None or nominal is None:
                return _empty_snk_snapshot("snk-api-unmatched", values)
            measured_kw = act_val * nominal * 0.001
            if measured_kw < 0 or measured_kw > 10_000:
                return _empty_snk_snapshot("snk-api-out-of-range", values)
            return PowerSnapshot(
                pv_kw=measured_kw,
                load_kw=None,
                grid_kw=None,
                timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                source="snk-api-measured-value",
                raw_excerpt=_compact_excerpt(json.dumps(values, ensure_ascii=True)),
            )
        except Exception as exc:  # noqa: BLE001
            self._trace(f"api:error {exc}")
            return PowerSnapshot(
                pv_kw=None,
                load_kw=None,
                grid_kw=None,
                timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                source="snk-api-error",
                raw_excerpt=str(exc),
            )

    def _snk_base_url(self) -> str:
        parsed = urllib.parse.urlparse(self.target_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _snk_api_access_token(self) -> str:
        state = "snk" + uuid.uuid4().hex[:12]
        base = self._snk_base_url()
        auth = self._api_post_json(
            f"{base}/_pxc_api/v1.8/auth/auth-token",
            {"response_type": "code", "state": state, "scope": "variables"},
        )
        code = auth.get("code")
        if not code:
            raise RuntimeError("SNK auth-token response did not include code.")
        access = self._api_post_json(
            f"{base}/_pxc_api/v1.8/auth/access-token",
            {
                "code": code,
                "grant_type": "authorization_code",
                "username": self.username or "",
                "password": self.password or "",
                "state": state,
            },
        )
        token = access.get("access_token")
        if not token:
            raise RuntimeError("SNK access-token response did not include access_token.")
        return str(token)

    def _snk_api_create_session(self, token: str) -> str:
        station_id = "codex-" + uuid.uuid4().hex[:10]
        payload = f"stationID={station_id}&timeout=30000"
        response = self._api_request(
            f"{self._snk_base_url()}/_pxc_api/v1.11/sessions/",
            method="POST",
            body=payload.encode("utf-8"),
            token=token,
            content_type="text/plain",
        )
        session_id = response.get("sessionID")
        if not session_id:
            raise RuntimeError("SNK session response did not include sessionID.")
        return str(session_id)

    def _snk_api_read_values(self, token: str, session_id: str, paths: list[str]) -> dict:
        query = urllib.parse.urlencode(
            {
                "pathPrefix": "Arp.Plc.Eclr/",
                "paths": ",".join(paths),
                "sessionID": session_id,
            },
            safe=",/[]",
        )
        response = self._api_request(
            f"{self._snk_base_url()}/_pxc_api/v1.11/variables/?{query}",
            method="GET",
            token=token,
        )
        result: dict = {}
        for item in response.get("variables", []) or []:
            if isinstance(item, dict) and "path" in item:
                result[str(item["path"])] = item.get("value")
        return result

    def _api_post_json(self, url: str, payload: dict) -> dict:
        return self._api_request(
            url,
            method="POST",
            body=json.dumps(payload).encode("utf-8"),
            content_type="text/plain",
        )

    def _api_request(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        token: str | None = None,
        content_type: str = "application/json",
    ) -> dict:
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("Content-Type", content_type)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, context=ctx, timeout=45) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw) if raw else {}

    def _scrape_once_session(self, attempt: int) -> PowerSnapshot:
        with sync_playwright() as p:
            self._trace("playwright:started")
            launch_kwargs = {
                "headless": self.headless,
            }
            if not self.headless:
                args = [
                    "--window-size=1600,1000",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--disable-background-timer-throttling",
                ]
                if self.window_mode == "offscreen":
                    args.append("--window-position=-2400,-2400")
                else:
                    args.append("--start-maximized")
                launch_kwargs["args"] = args
            self._trace(f"browser:launch headless={self.headless}")
            browser = p.chromium.launch(**launch_kwargs)
            self._trace("browser:launched")
            context_kwargs = {
                "ignore_https_errors": True,
                "viewport": {"width": 1600, "height": 1000},
            }

            context: BrowserContext = browser.new_context(**context_kwargs)
            self._trace("context:created")
            try:
                page = context.new_page()
                self._trace("page:created")
                page.set_default_timeout(self.browser_timeout_ms)
                page.goto(self.target_url, wait_until="domcontentloaded")
                self._trace("page:loaded")
                self._wait_for_hmi_canvas_initialized(page)
                self._wait_for_non_loading_canvas(page)
                self._maybe_login_wincc_webux(page)
                self._trace("login:attempted")
                self._open_home_tab(page)
                self._trace("home:attempted")
                self._save_debug_artifacts(page, stage=f"attempt-{attempt}-post-login")
                text, pv_kw, source = self._wait_for_measured_value(page)
                self._trace(f"value:done source={source} pv_kw={pv_kw}")
                self._save_debug_artifacts(page, stage=f"attempt-{attempt}-post-read", text=text)
                return PowerSnapshot(
                    pv_kw=pv_kw,
                    load_kw=None,
                    grid_kw=None,
                    timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
                    source=source,
                    raw_excerpt=_compact_excerpt(text),
                )
            finally:
                try:
                    self._trace("context:closing")
                    context.close()
                finally:
                    self._trace("browser:closing")
                    browser.close()
                    self._trace("browser:closed")

    def _wait_for_measured_value(self, page) -> tuple[str, Optional[float], str]:
        last_text = ""
        for _ in range(self.value_wait_attempts):
            try:
                last_text = self._collect_page_text(page)
            except Exception:
                page.wait_for_timeout(1000)
                continue
            pv_kw = _extract_measured_kw(last_text)
            if pv_kw is not None:
                self._debug_events.append("value:dom")
                return last_text, pv_kw, "snk-measured-value"

            # This HMI renders the useful values on a canvas, so DOM text is
            # usually empty. OCR early instead of waiting through the full loop.
            self._trace("value:ocr:start")
            ocr_text, ocr_kw = _extract_measured_kw_ocr(page)
            self._trace(f"value:ocr:end found={ocr_kw is not None}")
            if ocr_kw is not None:
                merged = f"{last_text}\n{ocr_text}".strip() if last_text else ocr_text
                self._debug_events.append("value:ocr")
                return merged, ocr_kw, "snk-measured-value-ocr"

            page.wait_for_timeout(self.value_wait_sleep_ms)
        self._debug_events.append("value:unmatched")
        return last_text, None, "snk-unmatched"

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
        return "\n".join(chunks)

    def _wait_for_hmi_canvas_initialized(self, page) -> bool:
        try:
            self._trace("hmi:init:wait")
            page.wait_for_function(
                """() => {
                    const canvas = document.querySelector('#frontCanvas');
                    return !!canvas && canvas.width >= 1000 && canvas.height >= 700;
                }""",
                timeout=150_000,
            )
            page.wait_for_timeout(2_000)
            self._trace("hmi:init:ready")
            return True
        except Exception:
            self._trace("hmi:init:timeout")
            return False

    def _wait_for_non_loading_canvas(self, page) -> bool:
        self._trace("hmi:visual-ready:wait")
        deadline = datetime.now(tz=timezone.utc).timestamp() + 180
        while datetime.now(tz=timezone.utc).timestamp() < deadline:
            image = _read_front_canvas_image(page)
            if image is not None and not _is_blank_or_loading_canvas(image):
                self._trace("hmi:visual-ready:ready")
                return True
            try:
                page.wait_for_timeout(2_000)
            except Exception:
                break
        self._trace("hmi:visual-ready:timeout")
        return False

    def _maybe_login_wincc_webux(self, page) -> bool:
        if not (self.username and self.password):
            return False
        if self._maybe_login_modal(page):
            return True
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

    def _maybe_login_modal(self, page) -> bool:
        if not (self.username and self.password):
            return False

        if self._looks_logged_in(page):
            self._debug_events.append("login:already")
            return True

        canvas_success = self._maybe_login_modal_canvas(page)
        if canvas_success:
            self._wait_for_logged_in(page)
            self._debug_events.append("login:canvas")
            return True

        dom_success = False
        try:
            dom_success = self._maybe_login_modal_dom(page)
        except Exception:
            dom_success = False

        if dom_success and self._submit_login_canvas(page):
            self._wait_for_logged_in(page)
            self._debug_events.append("login:dom-canvas-submit")
            return True

        self._debug_events.append("login:failed")
        return False

    def _maybe_login_modal_dom(self, page) -> bool:
        user_sel = "#L1N3username"
        pwd_sel = "#L1N6password"
        try:
            self._trace("login:dom:start")
            canvas = page.locator("#frontCanvas")
            if canvas.count() > 0:
                box = canvas.first.bounding_box()
                if box:
                    self._trace("login:dom:click-user")
                    page.mouse.click(box["x"] + box["width"] * 0.505, box["y"] + box["height"] * 0.407)
                    page.wait_for_timeout(500)
            try:
                page.locator(user_sel).wait_for(state="attached", timeout=10_000)
                self._trace("login:dom:user-attached")
            except Exception:
                self._trace("login:dom:user-missing")
                return False
            if canvas.count() > 0:
                box = canvas.first.bounding_box()
                if box:
                    self._trace("login:dom:click-pass")
                    page.mouse.click(box["x"] + box["width"] * 0.505, box["y"] + box["height"] * 0.518)
                    page.wait_for_timeout(500)
            try:
                page.locator(pwd_sel).wait_for(state="attached", timeout=10_000)
                self._trace("login:dom:pass-attached")
            except Exception:
                self._trace("login:dom:pass-missing")
                return False
            page.locator(user_sel).fill(self.username, force=True)
            page.wait_for_timeout(300)
            page.locator(pwd_sel).fill(self.password, force=True)
            page.evaluate(
                """({ userSel, pwdSel }) => {
                    for (const sel of [userSel, pwdSel]) {
                        const el = document.querySelector(sel);
                        if (!el) continue;
                        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: el.value }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }""",
                {"userSel": user_sel, "pwdSel": pwd_sel},
            )
            self._trace("login:dom:filled")
            try:
                page.locator(pwd_sel).press("Enter", timeout=2_000)
            except Exception:
                pass
            return True
        except Exception:
            self._trace("login:dom:exception")
            return False

    def _submit_login_canvas(self, page) -> bool:
        try:
            canvas = page.locator("#frontCanvas")
            if canvas.count() == 0:
                return False
            box = canvas.first.bounding_box()
            if not box:
                return False

            login_x = box["x"] + box["width"] * 0.463
            login_y = box["y"] + box["height"] * 0.624
            login_offsets = (
                (0, 0),
                (-24, 0),
                (24, 0),
                (0, -10),
                (0, 10),
                (-16, -8),
                (16, 8),
                (-28, -10),
                (28, 10),
            )
            for _ in range(2):
                try:
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(700)
                except Exception:
                    pass
            for _round in range(4):
                for dx, dy in login_offsets:
                    self._dispatch_canvas_click(page, 0.463, 0.624)
                    page.mouse.click(login_x + dx, login_y + dy)
                    page.wait_for_timeout(850)
                    if self._looks_logged_in(page):
                        return True
            return True
        except Exception:
            return False

    def _maybe_login_modal_canvas(self, page) -> bool:
        if not (self.username and self.password):
            return False
        try:
            self._trace("login:canvas:start")
            canvas = page.locator("#frontCanvas")
            if canvas.count() == 0:
                self._trace("login:canvas:no-canvas")
                return False
            box = canvas.first.bounding_box()
            if not box:
                self._trace("login:canvas:no-box")
                return False

            def c(rel_x: float, rel_y: float) -> tuple[float, float]:
                return (box["x"] + box["width"] * rel_x, box["y"] + box["height"] * rel_y)

            # Coordinates derived from the rendered login modal at 1600x1000.
            user_rel = (0.505, 0.407)
            pass_rel = (0.505, 0.518)
            login_rel = (0.463, 0.624)
            user_x, user_y = c(0.505, 0.407)
            pass_x, pass_y = c(0.505, 0.518)
            login_x, login_y = c(0.463, 0.624)

            self._dispatch_canvas_click(page, *user_rel)
            page.mouse.click(user_x, user_y, click_count=2)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.keyboard.type(self.username, delay=40)

            self._dispatch_canvas_click(page, *pass_rel)
            page.mouse.click(pass_x, pass_y, click_count=2)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.keyboard.type(self.password, delay=40)

            try:
                page.locator("#L1N3username").fill(self.username, force=True, timeout=2_000)
                page.locator("#L1N6password").fill(self.password, force=True, timeout=2_000)
                page.evaluate(
                    """({ username, password }) => {
                        const user = document.querySelector('#L1N3username');
                        const pwd = document.querySelector('#L1N6password');
                        const apply = (el, value) => {
                            if (!el) return;
                            el.value = value;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        };
                        apply(user, username);
                        apply(pwd, password);
                    }""",
                    {"username": self.username, "password": self.password},
                )
            except Exception:
                pass

            login_offsets = (
                (0, 0),
                (-24, 0),
                (24, 0),
                (0, -10),
                (0, 10),
                (-16, -8),
                (16, 8),
                (-28, -10),
                (28, 10),
            )
            for _ in range(3):
                try:
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(200)
                except Exception:
                    pass

            for _ in range(4):
                try:
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(900)
                    if self._looks_logged_in(page):
                        return True
                except Exception:
                    pass

            for _round in range(4):
                for dx, dy in login_offsets:
                    self._dispatch_canvas_click(page, *login_rel)
                    page.mouse.click(login_x + dx, login_y + dy)
                    page.wait_for_timeout(850)
                    if self._looks_logged_in(page):
                        return True

            if self._fill_visible_login_inputs(page):
                self._trace("login:canvas:second-pass-filled")
                for _round in range(2):
                    for dx, dy in login_offsets:
                        self._dispatch_canvas_click(page, *login_rel)
                        page.mouse.click(login_x + dx, login_y + dy)
                        page.wait_for_timeout(700)
                        if self._looks_logged_in(page):
                            return True

            if self.post_login_wait_ms > 0:
                page.wait_for_timeout(self.post_login_wait_ms)
            self._trace("login:canvas:done")
            return True
        except Exception:
            self._trace("login:canvas:exception")
            return False

    def _fill_visible_login_inputs(self, page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """({ username, password }) => {
                        const user = document.querySelector('#L1N3username');
                        const pwd = document.querySelector('#L1N6password');
                        if (!user || !pwd) return false;
                        user.value = username;
                        pwd.value = password;
                        user.dispatchEvent(new Event('input', { bubbles: true }));
                        pwd.dispatchEvent(new Event('input', { bubbles: true }));
                        user.dispatchEvent(new Event('change', { bubbles: true }));
                        pwd.dispatchEvent(new Event('change', { bubbles: true }));
                        return user.value === username && pwd.value === password;
                    }""",
                    {"username": self.username, "password": self.password},
                )
            )
        except Exception:
            return False

    def _dispatch_canvas_click(self, page, rel_x: float, rel_y: float) -> None:
        try:
            page.evaluate(
                """({ relX, relY }) => {
                    const canvas = document.querySelector('#frontCanvas');
                    if (!canvas) return false;
                    const rect = canvas.getBoundingClientRect();
                    const clientX = rect.left + rect.width * relX;
                    const clientY = rect.top + rect.height * relY;
                    const fire = (type) => {
                        const ev = new MouseEvent(type, {
                            bubbles: true,
                            cancelable: true,
                            view: window,
                            clientX,
                            clientY,
                            screenX: clientX,
                            screenY: clientY,
                            button: 0,
                            buttons: 1,
                        });
                        canvas.dispatchEvent(ev);
                    };
                    canvas.focus?.();
                    fire('mousemove');
                    fire('mousedown');
                    fire('mouseup');
                    fire('click');
                    return true;
                }""",
                {"relX": rel_x, "relY": rel_y},
            )
        except Exception:
            pass

    def _wait_for_logged_in(self, page) -> bool:
        if self._looks_logged_in(page):
            return True
        page.wait_for_timeout(max(self.post_login_wait_ms, 2_000))
        return self._looks_logged_in(page)

    def _looks_logged_in(self, page) -> bool:
        text = ""
        try:
            text = self._collect_page_text(page)
        except Exception:
            text = ""

        normalized = re.sub(r"\s+", " ", str(text)).strip().lower()
        if normalized:
            if "log in" in normalized and "password" in normalized and "user" in normalized:
                return False
            if "current user" in normalized and str(self.username or "").strip().lower() in normalized:
                return True
            if "measured value" in normalized:
                return True

        return False

    def _open_home_tab(self, page) -> None:
        candidates = (
            page.get_by_role("link", name=re.compile(r"^\s*Home\s*$", re.I)),
            page.get_by_role("button", name=re.compile(r"^\s*Home\s*$", re.I)),
            page.get_by_text(re.compile(r"^\s*Home\s*$", re.I), exact=False),
            page.locator("text=Home"),
        )
        loc = _first_present(candidates)
        if loc is None:
            return
        try:
            loc.first.click(force=True)
            page.wait_for_timeout(1000)
        except Exception:
            pass

    def _save_debug_artifacts(self, page, stage: str, text: str = "") -> None:
        if self.debug_artifact_dir is None:
            return
        try:
            self.debug_artifact_dir.mkdir(parents=True, exist_ok=True)
            stem = f"snk-{stage}"
            meta = {
                "stage": stage,
                "url": page.url,
                "title": page.title(),
                "events": list(self._debug_events),
            }
            (self.debug_artifact_dir / f"{stem}.json").write_text(
                json.dumps(meta, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            body_text = text
            if not body_text:
                try:
                    body_text = self._collect_page_text(page)
                except Exception:
                    body_text = ""
            (self.debug_artifact_dir / f"{stem}.txt").write_text(body_text or "", encoding="utf-8")
            try:
                html = page.content()
            except Exception:
                html = ""
            (self.debug_artifact_dir / f"{stem}.html").write_text(html or "", encoding="utf-8")
            try:
                page.screenshot(path=str(self.debug_artifact_dir / f"{stem}.png"), full_page=True, timeout=5_000)
            except Exception:
                pass
        except Exception:
            pass

    def _trace(self, message: str) -> None:
        if self.debug_artifact_dir is None:
            return
        try:
            self.debug_artifact_dir.mkdir(parents=True, exist_ok=True)
            with (self.debug_artifact_dir / "trace.log").open("a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now(tz=timezone.utc).isoformat()} {message}\n")
        except Exception:
            pass


def _extract_measured_kw(text: str) -> Optional[float]:
    blocks = _iter_local_text_blocks(text)
    for block in blocks:
        for pattern in MEASURED_PATTERNS:
            m = pattern.search(block)
            if not m:
                continue
            return _parse_number(m.group(1))
    return None


def _first_float_value(values: dict, suffix: str) -> Optional[float]:
    for key, value in values.items():
        if str(key).endswith(suffix):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _empty_snk_snapshot(source: str, raw: object = "") -> PowerSnapshot:
    return PowerSnapshot(
        pv_kw=None,
        load_kw=None,
        grid_kw=None,
        timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
        source=source,
        raw_excerpt=_compact_excerpt(json.dumps(raw, ensure_ascii=True) if not isinstance(raw, str) else raw),
    )


def _extract_measured_kw_ocr(page) -> tuple[str, Optional[float]]:
    cv2_mod, np_mod = _get_cv2_np()
    rapid_ocr = _get_rapidocr_class()
    if cv2_mod is None or np_mod is None or rapid_ocr is None:
        return "", None
    try:
        image = _read_front_canvas_image(page)
        if image is None:
            return "", None
        if _is_blank_or_loading_canvas(image):
            return "", None
        engine = rapid_ocr()

        region_text, region_value = _extract_measured_kw_control_region_ocr(image, engine)
        if region_value is not None:
            return region_text, region_value

        # The full-page canvas OCR is very slow and has previously picked up
        # unrelated values. Keep SNK bounded to the Control card crop only.
        return region_text, None
    except Exception:
        return "", None


def _read_front_canvas_image(page):
    try:
        cv2_mod, np_mod = _get_cv2_np()
        if cv2_mod is None or np_mod is None:
            return None
        data_url = page.evaluate(
            """() => {
                const canvas = document.querySelector('#frontCanvas');
                if (!canvas || !canvas.width || !canvas.height) return null;
                try {
                    return canvas.toDataURL('image/png');
                } catch {
                    return null;
                }
            }"""
        )
        if not data_url or "," not in data_url:
            return None
        raw = base64.b64decode(str(data_url).split(",", 1)[1])
        arr = np_mod.frombuffer(raw, dtype=np_mod.uint8)
        return cv2_mod.imdecode(arr, cv2_mod.IMREAD_COLOR)
    except Exception:
        return None


def _is_blank_or_loading_canvas(image) -> bool:
    try:
        cv2_mod, _ = _get_cv2_np()
        if cv2_mod is None:
            return False
        gray = cv2_mod.cvtColor(image, cv2_mod.COLOR_BGR2GRAY)
        mean = float(gray.mean())
        std = float(gray.std())
        # Black/grey loading frames are visually flat and should not be sent to
        # OCR; they make RapidOCR spend minutes on an unreadable canvas.
        return mean < 25.0 or std < 8.0
    except Exception:
        return False


def _extract_measured_kw_control_region_ocr(image, engine) -> tuple[str, Optional[float]]:
    cv2_mod, np_mod = _get_cv2_np()
    if cv2_mod is None or np_mod is None:
        return "", None
    h, w = image.shape[:2]
    region_specs = (
        # Main "Control" card on the right-center of the HMI.
        (0.47, 0.26, 0.74, 0.54),
        # Tighter inner table area where measured value sits.
        (0.50, 0.30, 0.72, 0.46),
        # Fallback slightly wider in case layout shifts.
        (0.44, 0.24, 0.78, 0.58),
    )
    for x1r, y1r, x2r, y2r in region_specs:
        x1 = max(0, min(w - 1, int(w * x1r)))
        y1 = max(0, min(h - 1, int(h * y1r)))
        x2 = max(x1 + 1, min(w, int(w * x2r)))
        y2 = max(y1 + 1, min(h, int(h * y2r)))
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        if _is_blank_or_loading_canvas(crop):
            continue

        variants = [crop]
        try:
            gray = cv2_mod.cvtColor(crop, cv2_mod.COLOR_BGR2GRAY)
            variants.append(cv2_mod.cvtColor(gray, cv2_mod.COLOR_GRAY2BGR))
            enlarged = cv2_mod.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2_mod.INTER_CUBIC)
            variants.append(cv2_mod.cvtColor(enlarged, cv2_mod.COLOR_GRAY2BGR))
        except Exception:
            pass

        for variant in variants:
            try:
                result, _ = engine(variant)
            except Exception:
                continue
            if not result:
                continue
            structured = _ocr_result_to_structured_text(result)
            positioned_value = _extract_measured_kw_from_ocr_boxes(result)
            if positioned_value is not None:
                return structured, positioned_value
            value = _extract_measured_kw(structured)
            if value is not None:
                return structured, value
    return "", None


def _extract_measured_kw_from_ocr_boxes(result) -> Optional[float]:
    entries = []
    for item in result or []:
        try:
            box = item[0]
            text = str(item[1]).strip()
            if not text:
                continue
            xs = [float(pt[0]) for pt in box]
            ys = [float(pt[1]) for pt in box]
            entries.append(
                {
                    "text": text,
                    "x": sum(xs) / len(xs),
                    "y": sum(ys) / len(ys),
                    "x_min": min(xs),
                    "x_max": max(xs),
                    "y_min": min(ys),
                    "y_max": max(ys),
                }
            )
        except Exception:
            continue
    if not entries:
        return None

    measured_rows = []
    for entry in entries:
        lower = entry["text"].lower()
        if "measured" in lower:
            measured_rows.append(entry["y"])
            continue
        if "value" in lower and any(abs(entry["y"] - other["y"]) <= 18 and "measured" in other["text"].lower() for other in entries):
            measured_rows.append(entry["y"])
    if not measured_rows:
        return None

    measured_y = sum(measured_rows) / len(measured_rows)
    numeric_candidates = []
    for entry in entries:
        raw = entry["text"]
        if not re.fullmatch(r"\s*[0-9][0-9\s.,']*\s*", raw):
            continue
        value = _parse_number(raw)
        if value is None:
            continue
        # The actual-value column is to the right of the row label. Keep only
        # numbers aligned with the measured row, not setpoint/control rows.
        if abs(entry["y"] - measured_y) <= 20:
            numeric_candidates.append((entry["x"], value))
    if not numeric_candidates:
        return None

    # In the Control card crop, the measured power is the rightmost number on
    # the measured row.
    numeric_candidates.sort(key=lambda pair: pair[0], reverse=True)
    return numeric_candidates[0][1]


def _iter_local_text_blocks(text: str) -> list[str]:
    lines = [ln.strip() for ln in str(text).splitlines() if ln and ln.strip()]
    if not lines:
        compact = re.sub(r"\s+", " ", str(text)).strip()
        return [compact] if compact else []

    blocks: list[str] = []
    for i, line in enumerate(lines):
        blocks.append(line)
        if i + 1 < len(lines):
            blocks.append(f"{line} {lines[i + 1]}")
    return blocks


def _parse_number(raw: str) -> Optional[float]:
    cleaned = raw.strip().replace("\u00a0", " ").replace("'", "")
    cleaned = re.sub(r"\s+", "", cleaned)
    if cleaned.count(",") > 1 and cleaned.count(".") == 0:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None

