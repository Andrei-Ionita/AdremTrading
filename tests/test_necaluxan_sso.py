import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from power_reading.scrapers.necaluxan_scraper import (
    NecaluxanReadOnlyError,
    NecaluxanScraper,
    _VCOM_LOGIN_DESTINATION_RE,
)


class NecaluxanSSOTests(unittest.TestCase):
    def setUp(self):
        self.scraper = NecaluxanScraper(
            "https://www1.meteocontrol.de/vcom/", "test-user", "test-password"
        )

    def test_intermediate_login_redirects_are_not_destinations(self):
        for url in (
            "https://www1.meteocontrol.de/vcom/login-oauth",
            "https://www1.meteocontrol.de/vcom/default/login/index/",
            "https://www1.meteocontrol.de/vcom/login/process",
            "https://example.test/vcom/index/index",
        ):
            with self.subTest(url=url):
                self.assertIsNone(_VCOM_LOGIN_DESTINATION_RE.search(url))
        for url in (
            "https://auth.meteocontrol.com/realms/mc-cloud/protocol/openid-connect/auth",
            "https://www1.meteocontrol.de/vcom/index/index",
        ):
            self.assertIsNotNone(_VCOM_LOGIN_DESTINATION_RE.search(url))

    def test_cookie_banner_navigation_race_is_harmless(self):
        page = Mock()
        page.get_by_role.return_value.count.side_effect = PlaywrightError(
            "Locator.count: Execution context was destroyed, most likely because of a navigation"
        )
        self.scraper._dismiss_cookie_banner(page)

    def test_cookie_banner_does_not_hide_unrelated_errors(self):
        page = Mock()
        page.get_by_role.return_value.count.side_effect = PlaywrightError("Browser closed")
        with self.assertRaises(PlaywrightError):
            self.scraper._dismiss_cookie_banner(page)

    def test_login_resumes_after_navigation_race(self):
        page = Mock()
        with patch.object(self.scraper, "_login_vcom_once", side_effect=[
            PlaywrightError("Execution context was destroyed"), None
        ]) as login:
            self.scraper._login_vcom(page)
        self.assertEqual(login.call_count, 2)
        page.goto.assert_not_called()

    def test_navigation_retry_is_bounded(self):
        with patch.object(self.scraper, "_login_vcom_once", side_effect=PlaywrightError(
            "Execution context was destroyed"
        )) as login:
            with self.assertRaises(NecaluxanReadOnlyError):
                self.scraper._login_vcom(Mock())
        self.assertEqual(login.call_count, 3)

    def test_login_does_not_retry_other_errors(self):
        with patch.object(self.scraper, "_login_vcom_once", side_effect=PlaywrightError(
            "Browser closed"
        )) as login:
            with self.assertRaises(PlaywrightError):
                self.scraper._login_vcom(Mock())
        login.assert_called_once()

    def test_sso_waits_past_intermediate_redirect(self):
        page = Mock(url="https://www1.meteocontrol.de/vcom/login-oauth")
        def arrive(*args, **kwargs):
            page.url = "https://www1.meteocontrol.de/vcom/index/index"
        page.wait_for_url.side_effect = arrive
        self.scraper._complete_meteocontrol_sso(page)
        page.wait_for_url.assert_called_once_with(
            _VCOM_LOGIN_DESTINATION_RE, timeout=self.scraper.browser_timeout_ms
        )
        page.locator.assert_not_called()

    def test_username_discovery_refills_username_and_submits_once_per_step(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route("https://auth.meteocontrol.com/**", lambda route: route.fulfill(
                    content_type="text/html", body="""
                    <form><input id="username"><input id="password" type="password" hidden>
                    <input id="kc-login" type="submit" value="Continue"></form>
                    <script>
                    window.submitted=[];
                    document.querySelector('form').onsubmit=e=>{
                        e.preventDefault();
                        const user=document.querySelector('#username'), pass=document.querySelector('#password');
                        submitted.push({username:user.value,password:pass.hidden?null:pass.value});
                        if(pass.hidden){pass.hidden=false;user.value='';}
                    };
                    </script>
                    """
                ))
                page.goto("https://auth.meteocontrol.com/test")
                self.scraper._complete_meteocontrol_sso(page)
                self.assertEqual(page.evaluate("submitted"), [
                    {"username": "test-user", "password": None},
                    {"username": "test-user", "password": "test-password"},
                ])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
