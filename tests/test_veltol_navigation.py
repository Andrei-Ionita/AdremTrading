import unittest
from unittest.mock import Mock
from power_reading.scrapers.veltol_scraper import VeltolScraper


class VeltolNavigationTests(unittest.TestCase):
    def scraper(self):
        scraper = VeltolScraper('https://example.test/locations/3068/')
        scraper._accept_cookies = Mock()
        scraper._maybe_login = Mock()
        scraper._wait_for_dashboard = Mock()
        return scraper

    def test_successful_normalized_location_not_reloaded(self):
        scraper = self.scraper()
        page = Mock(url='https://example.test/locations/3068?tab=energy')
        scraper._open_dashboard(page)
        page.goto.assert_called_once()
        scraper._maybe_login.assert_called_once()
        scraper._wait_for_dashboard.assert_called_once()

    def test_late_login_redirect_reauthenticated_once(self):
        scraper = self.scraper()
        page = Mock(url='https://example.test/locations/3068')
        def readiness():
            page.url = 'https://example.test/auth/login'
            raise RuntimeError('dashboard missing')
        def first_wait(_page):
            if scraper._wait_for_dashboard.call_count == 1:
                readiness()
        scraper._wait_for_dashboard.side_effect = first_wait
        scraper._open_dashboard(page)
        self.assertEqual(scraper._maybe_login.call_count, 2)
        self.assertEqual(scraper._wait_for_dashboard.call_count, 2)

    def test_missing_telemetry_does_not_trigger_login_loop(self):
        scraper = self.scraper()
        page = Mock(url='https://example.test/locations/3068')
        scraper._wait_for_dashboard.side_effect = RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):
            scraper._open_dashboard(page)
        scraper._maybe_login.assert_called_once()

    def test_different_location_is_navigated_then_authenticated(self):
        scraper = self.scraper()
        page = Mock(url='https://example.test/locations/999')
        scraper._open_dashboard(page)
        self.assertEqual(page.goto.call_count, 2)
        self.assertEqual(scraper._maybe_login.call_count, 2)
