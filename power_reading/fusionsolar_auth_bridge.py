"""Private SSH-stdio bridge for human FusionSolar verification. Never a public server."""
from __future__ import annotations

import base64
import json
import sys
import tempfile

from playwright.sync_api import sync_playwright

from .database import _connection_scope
from .fusionsolar_session import FusionSolarSessionStore
from .service import _ASSETS, _build_scraper
from .scrapers.fusionsolar_scraper import FusionSolarVerificationRequired


class VerificationBridge:
    def __init__(self, playwright, assets=("elnet", "horeco")):
        if not assets or any(asset not in {"elnet", "horeco", "renewable_energy_holding"} for asset in assets):
            raise ValueError("Select a supported FusionSolar account.")
        self.playwright = playwright
        self.pending = iter(dict.fromkeys(assets))
        self.completed = []
        self.context = None
        self.directory = None
        self.asset = None
        with _connection_scope() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""CREATE TABLE IF NOT EXISTS fusion_solar_sessions (
                    asset VARCHAR(64) PRIMARY KEY, encrypted_state TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
            conn.commit()

    def close(self):
        if self.context:
            self.context.close()
            self.context = None
        if self.directory:
            self.directory.cleanup()
            self.directory = None

    def next_asset(self):
        self.close()
        self.asset = next(self.pending, None)
        if self.asset is None:
            return self.status()
        self.scraper = _build_scraper(_ASSETS[self.asset], headless=True)
        self.scraper.session_store = FusionSolarSessionStore(
            self.asset, self.scraper.username, self.scraper.password, self.scraper.user_data_dir)
        self.directory = tempfile.TemporaryDirectory(prefix="fusion-human-")
        self.context = self.playwright.chromium.launch_persistent_context(
            self.directory.name, headless=True, viewport={"width": 1920, "height": 1200},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            args=["--disable-blink-features=AutomationControlled"])
        self.context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)
        try:
            self.scraper._open_session(self.context, self.page, restore_session=False)
            return self.complete_asset()
        except FusionSolarVerificationRequired:
            if self.scraper.region_name:
                self.scraper._select_region_on_login(self.page)
            self.page.locator('#username').fill(self.scraper.username)
            self.page.locator('#value').fill(self.scraper.password)
            return self.status()

    def status(self):
        result = {"asset": self.asset, "completed": self.completed,
                  "status": "complete" if self.asset is None else "verification_required"}
        if self.asset is not None:
            result['image'] = base64.b64encode(self.page.screenshot(
                mask=[self.page.locator('#username'), self.page.locator('#value')],
                clip={"x": 480, "y": 350, "width": 1080, "height": 250})).decode()
        return result

    def submit(self, code: str):
        if self.asset is None:
            return self.status()
        if not code or len(code) > 16 or not code.isalnum():
            return {**self.status(), "error": "Enter the verification code shown in the image."}
        field = self.page.locator('#verificationCode:visible, #twoFactorCode:visible').first
        field.fill(code)
        # This submission is performed only in response to human-entered verification.
        submit = self.page.locator('#submitDataverify:visible, #submitData:visible')
        if submit.count():
            submit.first.click()
        else:
            self.page.locator('.loginBtn').first.click()
        try:
            self.scraper._wait_for_authenticated_page(self.page)
        except RuntimeError:
            return {**self.status(), "error": "Verification did not complete. Check the current image."}
        return self.complete_asset()

    def complete_asset(self):
        self.scraper._wait_for_authenticated_page(self.page)
        self.scraper._wait_for_plant_list(self.page)
        self.scraper.save_session(self.context, self.page)
        self.completed.append(self.asset)
        return self.next_asset()


def main(assets=("elnet", "horeco")):
    with sync_playwright() as playwright:
        bridge = VerificationBridge(playwright, assets)
        try:
            for line in sys.stdin:
                try:
                    request = json.loads(line)
                    command = request.get('command')
                    if command == 'start':
                        response = bridge.status() if bridge.asset or bridge.completed else bridge.next_asset()
                    elif command == 'submit':
                        response = bridge.submit(str(request.get('code', '')))
                    elif command == 'status':
                        response = bridge.status()
                    elif command == 'resume':
                        response = bridge.complete_asset() if bridge.asset else bridge.status()
                    elif command == 'stop':
                        break
                    else:
                        response = {"status": "error", "error": "Unknown command."}
                except Exception as exc:
                    # Browser exceptions can embed filled form values; never emit them.
                    response = {"status": "error", "error": type(exc).__name__}
                print(json.dumps(response), flush=True)
        finally:
            bridge.close()
