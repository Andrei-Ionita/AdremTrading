import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from power_reading.scrapers.fusionsolar_scraper import (
    FusionSolarAuthenticationError,
    FusionSolarVerificationRequired,
    FusionSolarScraper,
    PowerSnapshot,
)


class FusionSolarAuthenticationTests(unittest.TestCase):
    def scraper(self, **kwargs):
        return FusionSolarScraper('https://example.test/unisso/login.action',
                                 username='test-user', password='test-secret', **kwargs)

    def test_stuck_session_retries_once_in_clean_profile_and_cleans_up(self):
        scraper = self.scraper()
        snapshot = PowerSnapshot(0.0, None, None, '2026-10-02T10:00:00Z', 'test', '')
        profiles = []

        def read(profile, *, restore_session=True):
            profiles.append(profile)
            if len(profiles) == 1:
                raise FusionSolarAuthenticationError('login stuck')
            self.assertTrue(profile.is_dir())
            self.assertFalse(restore_session)
            return snapshot

        with patch.object(scraper, '_scrape_once', side_effect=read):
            self.assertIs(scraper.scrape_once(), snapshot)
        self.assertEqual(profiles[0], scraper.user_data_dir)
        self.assertNotEqual(profiles[1], scraper.user_data_dir)
        self.assertFalse(profiles[1].exists())

    def test_successful_session_does_not_retry(self):
        scraper = self.scraper()
        with patch.object(scraper, '_scrape_once', return_value=object()) as read:
            scraper.scrape_once()
        read.assert_called_once_with(scraper.user_data_dir)

    def test_clean_login_failure_is_bounded_and_cleans_up(self):
        scraper = self.scraper()
        with patch.object(scraper, '_scrape_once', side_effect=FusionSolarAuthenticationError('failed')) as read:
            with self.assertRaises(FusionSolarAuthenticationError):
                scraper.scrape_once()
        self.assertEqual(read.call_count, 2)
        self.assertFalse(read.call_args.args[0].exists())

    def test_saved_session_only_never_logs_in_with_clean_profile(self):
        scraper = self.scraper(use_saved_session_only=True)
        with patch.object(scraper, '_scrape_once', side_effect=FusionSolarAuthenticationError('failed')) as read:
            with self.assertRaises(FusionSolarAuthenticationError):
                scraper.scrape_once()
        self.assertEqual(read.call_count, 1)

    def test_missing_credentials_do_not_retry(self):
        scraper = FusionSolarScraper('https://example.test')
        with patch.object(scraper, '_scrape_once', side_effect=FusionSolarAuthenticationError('missing')) as read:
            with self.assertRaises(FusionSolarAuthenticationError):
                scraper.scrape_once()
        self.assertEqual(read.call_count, 1)

    def test_non_authentication_errors_do_not_trigger_login_retries(self):
        scraper = self.scraper()
        with patch.object(scraper, '_scrape_once', side_effect=ValueError('invalid power')) as read:
            with self.assertRaises(ValueError):
                scraper.scrape_once()
        self.assertEqual(read.call_count, 1)

    def test_login_timeout_is_not_silently_accepted_or_leaked(self):
        page = Mock()
        page.locator.return_value.count.return_value = 0
        page.url = 'https://example.test/unisso/login.action?token=private'
        page.wait_for_function.side_effect = PlaywrightTimeoutError('private details')
        with self.assertRaises(FusionSolarAuthenticationError) as error:
            self.scraper()._wait_for_authenticated_page(page)
        self.assertNotIn('private', str(error.exception))
        self.assertNotIn('http', str(error.exception))

    def test_login_does_not_continue_when_page_not_ready(self):
        page = Mock()
        page.wait_for_function.side_effect = PlaywrightTimeoutError('timeout')
        scraper = self.scraper()
        with patch.object(scraper, '_click_login') as login:
            with self.assertRaises(FusionSolarAuthenticationError):
                scraper._maybe_login(page)
        login.assert_not_called()

    def test_unsuccessful_submit_is_not_treated_as_authenticated(self):
        page = Mock(url='https://example.test/unisso/login.action')
        page.locator.return_value.count.return_value = 0
        page.wait_for_function.side_effect = [True, PlaywrightTimeoutError('timeout')]
        scraper = self.scraper()
        with patch.object(scraper, '_click_login') as login:
            with self.assertRaisesRegex(FusionSolarAuthenticationError, 'authenticated plant portal'):
                scraper._maybe_login(page)
        login.assert_called_once()

    def test_authenticated_session_does_not_resubmit_credentials(self):
        page = Mock(url='https://example.test/cloud.html#/home/list')
        page.locator.return_value.count.return_value = 0
        scraper = self.scraper()
        with patch.object(scraper, '_click_login') as login:
            scraper._maybe_login(page)
        login.assert_not_called()

    def test_browser_uses_recovery_profile_and_closes_before_retry(self):
        scraper = self.scraper()
        playwright = MagicMock()
        launch = playwright.__enter__.return_value.chromium.launch_persistent_context
        with tempfile.TemporaryDirectory() as directory, patch(
            'power_reading.scrapers.fusionsolar_scraper.sync_playwright', return_value=playwright
        ), patch.object(scraper, '_maybe_login', side_effect=FusionSolarAuthenticationError('failed')):
            with self.assertRaises(FusionSolarAuthenticationError):
                scraper._scrape_once(Path(directory))
            self.assertEqual(launch.call_args.kwargs['user_data_dir'], str(Path(directory).resolve()))
        launch.return_value.close.assert_called_once_with()

    def test_saved_session_is_authenticated_before_extracting_power(self):
        scraper = self.scraper(use_saved_session_only=True)
        playwright = MagicMock()
        with tempfile.TemporaryDirectory() as directory, patch(
            'power_reading.scrapers.fusionsolar_scraper.sync_playwright', return_value=playwright
        ), patch.object(scraper, '_wait_for_authenticated_page', side_effect=FusionSolarAuthenticationError('expired')), patch.object(
            scraper, '_extract_current_power_from_table'
        ) as extract:
            with self.assertRaises(FusionSolarAuthenticationError):
                scraper._scrape_once(Path(directory))
        extract.assert_not_called()

    def test_plant_list_waits_for_requested_plant_not_only_app_shell(self):
        page = Mock(url='https://example.test/cloud.html#/home/list')
        scraper = self.scraper(plant_name='CEF HORECO Costesti')
        scraper._wait_for_plant_list(page)
        self.assertEqual(page.wait_for_function.call_args.kwargs['arg'], 'cef horeco costesti')
        self.assertEqual(page.wait_for_function.call_args.kwargs['timeout'], 15000)

    def test_overview_wait_accepts_requested_station_instead_of_requiring_table(self):
        page = Mock(url='https://example.test/cloud.html#/view/station/NE=123/overview')
        self.scraper(plant_name='Elnet Biomasa.GR')._wait_for_plant_list(page)
        script = page.wait_for_function.call_args.args[0]
        self.assertIn("location.hash.startsWith('#/view/station/')", script)
        self.assertIn("text.includes(plant) && text.includes('active power')", script)
        self.assertEqual(page.wait_for_function.call_args.kwargs['arg'], 'elnet biomasa.gr')

    def test_missing_plant_row_fails_explicitly(self):
        page = Mock(url='https://example.test/uniportal/pvmswebsite/cloud.html#/home/list')
        page.wait_for_function.side_effect = PlaywrightTimeoutError('private details')
        with self.assertRaisesRegex(RuntimeError, 'requested plant row'):
            self.scraper(plant_name='CEF HORECO Costesti')._wait_for_plant_list(page)

    def test_public_portal_redirect_is_an_auth_failure_not_missing_plant(self):
        page = Mock(url='https://eu5.fusionsolar.huawei.com/uniportal/pvmswebsite/cloud.html#/home/list')

        def redirected(*args, **kwargs):
            page.url = 'https://eu5.fusionsolar.huawei.com/uniportal/portal'
            raise PlaywrightTimeoutError('private details')

        page.wait_for_function.side_effect = redirected
        with self.assertRaises(FusionSolarAuthenticationError):
            self.scraper(plant_name='CEF HORECO Costesti')._wait_for_plant_list(page)

    def test_login_without_verification_clicks_button(self):
        page, button, password = Mock(), Mock(), Mock()
        page.locator.return_value.count.return_value = 0
        button.count.return_value = 1
        self.scraper()._click_login(page, button, password)
        button.first.click.assert_called_once_with()

    def test_login_without_button_submits_with_enter(self):
        page, button, password = Mock(), Mock(), Mock()
        page.locator.return_value.count.return_value = 0
        button.count.return_value = 0
        self.scraper()._click_login(page, button, password)
        password.first.press.assert_called_once_with('Enter')

    def test_visible_verification_prevents_login_submission(self):
        page, button, password = Mock(), Mock(), Mock()
        page.locator.return_value.count.return_value = 1
        with self.assertRaises(FusionSolarVerificationRequired):
            self.scraper()._click_login(page, button, password)
        button.first.click.assert_not_called()
        password.first.press.assert_not_called()

    def test_verification_challenge_never_retries_in_clean_session(self):
        scraper = self.scraper()
        with patch.object(scraper, '_scrape_once', side_effect=FusionSolarVerificationRequired('verification')) as read:
            with self.assertRaises(FusionSolarVerificationRequired):
                scraper.scrape_once()
        self.assertEqual(read.call_count, 1)

    def test_verification_after_submission_is_not_an_auth_timeout(self):
        page = Mock()
        page.wait_for_function.side_effect = PlaywrightTimeoutError('timeout')
        page.locator.return_value.count.return_value = 1
        with self.assertRaises(FusionSolarVerificationRequired):
            self.scraper()._wait_for_authenticated_page(page)


if __name__ == '__main__':
    unittest.main()
