import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from playwright.sync_api import Error as PlaywrightError
from power_reading.scrapers.veltol_scraper import (
    VeltolScraper, VeltolDashboardNotReady, VeltolTelemetryError, _veltol_power_snapshot,
    _veltol_response_snapshot,
)


class VeltolStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.payload = {'data': {'status': {
            'location': {'id': 3068, 'isDataAvailable': True},
            'controllers': [{'isConnected': True}]}, 'data': {
                'solar': {'active': True, 'supported': True, 'measuredAt': self.now.timestamp(),
                    'measurements': {'solarExport': {'type': 'POWER', 'value': 71802, 'unit': 'W'}}},
                'grid': {'basePower': {'value': 68620, 'unit': 'W'}},
                'other': {'basePower': {'value': 3782, 'unit': 'W'}}}}}

    def parse(self, payload=None):
        return _veltol_power_snapshot(payload or self.payload, 3068, now=self.now)

    def test_actual_solar_power_and_measurement_timestamp(self):
        result = self.parse()
        self.assertAlmostEqual(result.pv_kw, 71.802)
        self.assertAlmostEqual(result.grid_kw, 68.62)
        self.assertEqual(result.timestamp_utc, self.now.isoformat())
        self.assertEqual(result.source, 'veltol-statistics')

    def test_zero_is_valid_and_units_are_converted(self):
        for value, unit, expected in [(0, 'W', 0), (1.2, 'MW', 1200), (40, 'kW', 40)]:
            self.payload['data']['data']['solar']['measurements']['solarExport'].update(value=value, unit=unit)
            self.assertEqual(self.parse().pv_kw, expected)

    def test_rejects_invalid_power_and_never_uses_grid_or_capacity(self):
        for value, unit in [(None, 'W'), (True, 'W'), (-1, 'W'), (float('nan'), 'W'),
                            (float('inf'), 'W'), ('100', 'W'), (1, 'kWh'), (1e308, 'MW')]:
            with self.subTest(value=value, unit=unit):
                self.payload['data']['data']['solar']['measurements']['solarExport'].update(value=value, unit=unit)
                with self.assertRaises(VeltolTelemetryError):
                    self.parse()

    def test_rejects_wrong_location(self):
        with self.assertRaises(VeltolTelemetryError):
            _veltol_power_snapshot(self.payload, 999, now=self.now)

    def test_rejects_offline_or_unavailable_data(self):
        for field in ('connection', 'available', 'active', 'supported'):
            payload = copy.deepcopy(self.payload)
            if field == 'connection':
                payload['data']['status']['controllers'][0]['isConnected'] = False
            elif field == 'available':
                payload['data']['status']['location']['isDataAvailable'] = False
            else:
                payload['data']['data']['solar'][field] = False
            with self.assertRaises(VeltolTelemetryError):
                self.parse(payload)

    def test_rejects_stale_future_or_missing_timestamp(self):
        for stamp in (self.now.timestamp()-301, self.now.timestamp()+61, None, True, float('nan')):
            self.payload['data']['data']['solar']['measuredAt'] = stamp
            with self.assertRaises(VeltolTelemetryError):
                self.parse()

    def test_authenticated_api_read_does_not_require_dashboard_labels(self):
        scraper = VeltolScraper('https://example.test/locations/3068', headless=True)
        import power_reading.scrapers.veltol_scraper as module
        original = module.sync_playwright
        with original() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                context.route('https://example.test/locations/3068', lambda route: route.fulfill(
                    content_type='text/html', body="<p>Loading charts</p><script>fetch('/api/locations/3068/statistics')</script>"))
                context.route('https://example.test/api/locations/3068/statistics', lambda route: route.fulfill(
                    content_type='application/json', body=json.dumps(self.payload)))
                fake = Mock()
                fake.chromium.launch.return_value.new_context.return_value = context
                with patch.object(module, 'sync_playwright') as runtime:
                    runtime.return_value.__enter__.return_value = fake
                    result = scraper.scrape_once()
                self.assertAlmostEqual(result.pv_kw, 71.802)
            finally:
                browser.close()

    def test_bounded_retry_for_missing_response_or_connection_reset(self):
        for exc in (VeltolDashboardNotReady('loading'), PlaywrightError('net::ERR_CONNECTION_RESET')):
            scraper = VeltolScraper('https://example.test/locations/3068')
            with patch.object(scraper, '_scrape_once', side_effect=[exc, 'reading']) as attempt:
                self.assertEqual(scraper.scrape_once(), 'reading')
                self.assertEqual(attempt.call_count, 2)
            with patch.object(scraper, '_scrape_once', side_effect=exc) as attempt:
                with self.assertRaises(type(exc)):
                    scraper.scrape_once()
                self.assertEqual(attempt.call_count, 2)

    def test_no_retry_for_invalid_telemetry_or_rejected_login(self):
        for exc in (VeltolTelemetryError('stale'), RuntimeError('login did not complete'), PlaywrightError('unrelated')):
            scraper = VeltolScraper('https://example.test/locations/3068')
            with patch.object(scraper, '_scrape_once', side_effect=exc) as attempt:
                with self.assertRaises(type(exc)):
                    scraper.scrape_once()
                self.assertEqual(attempt.call_count, 1)

    def test_transient_statistics_http_errors_are_retryable(self):
        for status in (408, 500, 502, 503, 504):
            response = Mock(status=status)
            with self.assertRaises(VeltolDashboardNotReady):
                _veltol_response_snapshot(response, 3068)
            response.json.assert_not_called()

    def test_rejected_or_rate_limited_requests_are_not_retried(self):
        for status in (401, 403, 404, 429):
            with self.assertRaises(VeltolTelemetryError):
                _veltol_response_snapshot(Mock(status=status), 3068)

    def test_invalid_response_body_is_not_exposed(self):
        response = Mock(status=200)
        response.json.side_effect = ValueError('sensitive response body')
        with self.assertRaisesRegex(VeltolTelemetryError, '^HNG/Veltol statistics response is invalid.$'):
            _veltol_response_snapshot(response, 3068)


if __name__ == '__main__':
    unittest.main()
