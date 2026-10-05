import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import numpy as np
import pandas as pd

from portfolio_intraday import (
    ELNET_INTRADAY_CONFIG, RENEWABLE_ENERGY_HOLDING_INTRADAY_CONFIG as CONFIG,
    RENEWABLE_ENERGY_HOLDING_SCALE, PortfolioIntradayInputError,
    run_portfolio_intraday_forecast,
)
from power_reading.service import _ASSETS, _build_scraper, _credentials, read_asset, PowerReading
from power_reading.worker import _configured_assets
from power_reading.scrapers.fusionsolar_scraper import FusionSolarScraper, _extract_plant_pv_output_power_kw


class RenewableReaderTests(unittest.TestCase):
    def test_dedicated_railway_credentials_and_isolated_encrypted_session(self):
        env = {'RENEWABLE_ENERGY_HOLDING_USERNAME': 'test-user',
               'RENEWABLE_ENERGY_HOLDING_PASSWORD': 'test-password',
               'FUSIONSOLAR_USERNAME': 'another-user', 'FUSIONSOLAR_PASSWORD': 'another-password',
               'FUSIONSOLAR_PORTAL_URL': 'https://other-account.example/'}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, env, clear=True), patch(
            'power_reading.service._profile_dir', return_value=Path(directory)
        ) as profile:
            scraper = _build_scraper(_ASSETS['renewable_energy_holding'], headless=True)
        self.assertEqual((scraper.username, scraper.password), ('test-user', 'test-password'))
        self.assertEqual(scraper.session_store.asset, 'renewable_energy_holding')
        self.assertEqual(scraper.plant_name, 'Renewable Energy Holding Parc Popesti')
        self.assertEqual(scraper.region_name, 'region004')
        self.assertTrue(scraper.require_pv_output_power)
        self.assertNotIn('other-account', scraper.target_url)
        profile.assert_called_once_with('renewable_energy_holding')

    def test_missing_credentials_never_borrow_another_fusionsolar_account(self):
        with patch.dict(os.environ, {'FUSIONSOLAR_USERNAME': 'other', 'FUSIONSOLAR_PASSWORD': 'other'}, clear=True):
            self.assertEqual(_credentials(_ASSETS['renewable_energy_holding']), (None, None))

    def test_renewable_can_be_selected_by_worker_and_power_is_converted_to_mw(self):
        with patch.dict(os.environ, {'POWER_READING_ASSETS': 'renewable_energy_holding'}, clear=True):
            self.assertEqual(_configured_assets(), ['renewable_energy_holding'])
        scraper = Mock()
        scraper.scrape_once.return_value = Mock(pv_kw=1234.5, load_kw=None, grid_kw=None,
            timestamp_utc='2026-10-05T09:00:00+00:00', source='active-power-text', raw_excerpt='')
        with patch('power_reading.service._build_scraper', return_value=scraper):
            reading = read_asset('Renewable Energy Holding')
        self.assertEqual(reading.asset, 'renewable_energy_holding')
        self.assertEqual(reading.pv_mw, 1.2345)

    def test_confirmed_plant_pv_output_supports_units_and_genuine_zero(self):
        plant = 'Renewable Energy Holding Parc Popesti'
        for raw, kw in (('1.25 MW', 1250), ('1,234.5 kW', 1234.5), ('500 W', 0.5), ('0 kW', 0)):
            with self.subTest(raw=raw):
                text = f'{plant}\n2.69 MWh Yield today\nPV\nOutput power\nPV\n{raw}\nGrid\nCurrent power\n999 kW'
                self.assertEqual(_extract_plant_pv_output_power_kw(text, plant), kw)
                self.assertIsNone(_extract_plant_pv_output_power_kw(text, 'Another plant'))

    def test_unavailable_output_never_uses_energy_total_or_grid_power(self):
        plant = 'Renewable Energy Holding Parc Popesti'
        for raw in ('--kW', 'NaN kW', 'inf kW', '-1 kW', '2.69 MWh'):
            with self.subTest(raw=raw):
                text = f'{plant}\n2.69 MWh Yield today\nPV\nOutput power\nPV\n{raw}\nGrid\nCurrent power\n999 kW'
                self.assertIsNone(_extract_plant_pv_output_power_kw(text, plant))

    def test_unavailable_portal_value_is_not_recorded_as_zero(self):
        scraper = FusionSolarScraper('https://example.test', plant_name='Renewable Energy Holding Parc Popesti',
                                    require_pv_output_power=True)
        page = Mock()
        page.locator.return_value.first.inner_text.return_value = 'Renewable Energy Holding Parc Popesti PV Output power PV --kW'
        with self.assertRaisesRegex(RuntimeError, 'PV output power is unavailable'):
            scraper._read_required_pv_output_power(page)
        self.assertEqual(page.wait_for_timeout.call_count, 3)

    def test_numeric_reading_excludes_account_details_from_stored_excerpt(self):
        scraper = FusionSolarScraper('https://example.test', plant_name='Renewable Energy Holding Parc Popesti',
                                    require_pv_output_power=True)
        page = Mock()
        page.locator.return_value.first.inner_text.return_value = 'private-account Renewable Energy Holding Parc Popesti PV Output power PV 1.25 MW'
        snapshot = scraper._read_required_pv_output_power(page)
        self.assertEqual(snapshot.pv_kw, 1250)
        self.assertEqual(snapshot.source, 'pv-output-power-text')
        self.assertNotIn('private-account', snapshot.raw_excerpt)

    def test_browser_read_uses_only_plant_pv_output_and_always_closes(self):
        plant = 'Renewable Energy Holding Parc Popesti'
        scraper = FusionSolarScraper('https://example.test', plant_name=plant,
                                    require_pv_output_power=True)
        for raw, expected_kw in (('1.296MW', 1296), ('--kW', None)):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                playwright = MagicMock()
                context = playwright.__enter__.return_value.chromium.launch_persistent_context.return_value
                page = context.new_page.return_value
                page.locator.return_value.first.inner_text.return_value = (
                    f'{plant}\n4.46 MWh Yield today\nPV\nOutput power\nPV\n{raw}\n'
                    'Grid\nCurrent power\n999 kW'
                )
                with patch('power_reading.scrapers.fusionsolar_scraper.sync_playwright', return_value=playwright), patch.object(
                    scraper, '_open_session'
                ) as authenticate, patch.object(scraper, '_open_plant_if_needed') as open_plant, patch.object(
                    scraper, '_extract_current_power_from_table'
                ) as table, patch('power_reading.scrapers.fusionsolar_scraper._extract_flow_kw_ocr') as ocr:
                    if expected_kw is None:
                        with self.assertRaisesRegex(RuntimeError, 'PV output power is unavailable'):
                            scraper._scrape_once(Path(directory))
                    else:
                        self.assertEqual(scraper._scrape_once(Path(directory)).pv_kw, expected_kw)
                    authenticate.assert_called_once_with(context, page, restore_session=True)
                    open_plant.assert_called_once_with(page)
                    table.assert_not_called()
                    ocr.assert_not_called()
                context.close.assert_called_once_with()


class RenewableCorrectionTests(unittest.TestCase):
    def test_baseline_is_latest_elnet_forecast_not_either_corrected_workbook(self):
        self.assertEqual(CONFIG.dam_results_path, ELNET_INTRADAY_CONFIG.dam_results_path)
        self.assertEqual(CONFIG.weather_path, ELNET_INTRADAY_CONFIG.weather_path)
        self.assertEqual(CONFIG.baseline_scale, 2.37 / 2.7)
        self.assertEqual(CONFIG.baseline_scale, RENEWABLE_ENERGY_HOLDING_SCALE)
        self.assertNotEqual(CONFIG.intraday_results_path, ELNET_INTRADAY_CONFIG.intraday_results_path)

    def test_real_interval_power_drives_both_directions_without_compounding(self):
        origin = pd.Timestamp('2026-10-05 12:00', tz='Europe/Bucharest')
        targets = pd.date_range(origin + pd.Timedelta(minutes=15), origin.normalize() + pd.Timedelta(hours=23, minutes=45), freq='15min')
        baseline = pd.DataFrame({'Data': targets.tz_localize(None),
            'Interval': targets.hour * 4 + targets.minute // 15 + 1, 'Prediction': 0.27})
        weather = pd.DataFrame({'period_end': targets.tz_convert('UTC'), 'ghi': 100.0})
        weather.loc[weather.index[-1], 'ghi'] = 0
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = replace(CONFIG, dam_results_path=root/'elnet.xlsx', weather_path=root/'weather.csv',
                             intraday_results_path=root/'renewable_corrected.xlsx')
            baseline.to_excel(config.dam_results_path, index=False)
            weather.to_csv(config.weather_path, index=False)
            original_baseline = config.dam_results_path.read_bytes()
            for power_mw, energy in ((0.8, 0.2), (1.2, 0.3), (0, 0)):
                with self.subTest(power_mw=power_mw):
                    readings = [PowerReading('renewable_energy_holding', timestamp.isoformat(), power_mw,
                        None, None, 'test') for timestamp in pd.date_range(origin-pd.Timedelta(minutes=15), origin, freq='5min')]
                    getter = Mock(return_value=readings)
                    result = run_portfolio_intraday_forecast(config, now=origin, readings_getter=getter)
                    self.assertEqual(getter.call_args.args[0], 'renewable_energy_holding')
                    self.assertEqual(result.Prediction_DAM.iloc[0], 0.237)
                    self.assertEqual(result.Prediction_ID.iloc[0], energy)
                    self.assertAlmostEqual(result.Correction.iloc[0], energy-0.237)
                    self.assertEqual(result.Prediction_ID.iloc[-1], 0)
                    self.assertTrue(np.isfinite(result.Prediction_ID).all())
                    again = run_portfolio_intraday_forecast(config, now=origin, readings_getter=getter)
                    pd.testing.assert_frame_equal(result, again)
                    self.assertEqual(config.dam_results_path.read_bytes(), original_baseline)
            with self.assertRaises(PortfolioIntradayInputError):
                run_portfolio_intraday_forecast(config, now=origin, readings_getter=lambda *a, **kw: [])


if __name__ == '__main__':
    unittest.main()
