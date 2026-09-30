import tempfile
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import portfolio_intraday as portfolio
import elnet_intraday
import horeco_intraday
import hng_intraday
import incuba_intraday
from power_reading.service import PowerReading


ORIGIN = pd.Timestamp('2026-09-28 12:30', tz='Europe/Bucharest')
TARGETS = pd.date_range(ORIGIN + pd.Timedelta(minutes=15), ORIGIN.normalize() + pd.Timedelta(hours=23, minutes=45), freq='15min')
WEATHER = pd.DataFrame({'period_end': TARGETS.tz_convert('UTC'), 'ghi': 100})


def baseline(value):
    return pd.DataFrame({'Data': TARGETS.tz_localize(None),
                         'Interval': TARGETS.hour * 4 + TARGETS.minute // 15 + 1,
                         'Prediction': value})


class LiveAssetCorrectionTests(unittest.TestCase):
    def test_all_correction_runners_use_generated_forecasts_without_loading_models(self):
        special_runners = {
            'elnet': elnet_intraday.run_elnet_intraday_forecast,
            'horeco': horeco_intraday.run_horeco_intraday_forecast,
            'hng': hng_intraday.run_hng_intraday_forecast,
        }
        configs = [value for value in vars(portfolio).values()
                   if isinstance(value, portfolio.PortfolioIntradayConfig)]
        runners = [
            (config.asset_key,
             special_runners.get(config.asset_key, partial(portfolio.run_portfolio_intraday_forecast, config)),
             config.dam_results_path, config.baseline_scale)
            for config in configs
        ]
        runners.append(('incuba', incuba_intraday.run_incuba_intraday_forecast,
                        incuba_intraday.ADREM_DAM_RESULTS_PATH, incuba_intraday.ADREM_TO_INCUBA_SCALE))
        self.assertEqual(len(runners), 14)
        for asset, runner, forecast_path, scale in runners:
            readings = [PowerReading(asset, stamp.isoformat(), 0.08, None, None, 'test')
                        for stamp in pd.date_range(ORIGIN - pd.Timedelta(minutes=15), ORIGIN, freq='5min')]
            with (
                self.subTest(asset=asset),
                patch('joblib.load', side_effect=AssertionError('Corrections must not reload models')) as load_model,
                patch('pathlib.Path.is_file', return_value=True),
                patch('pathlib.Path.mkdir'),
                patch('pandas.read_excel', return_value=baseline(0.1)) as read_forecast,
                patch('pandas.read_csv', return_value=WEATHER),
                patch.object(pd.DataFrame, 'to_excel'),
            ):
                result = runner(now=ORIGIN, readings_getter=lambda *args, **kwargs: readings)
                read_forecast.assert_called_once_with(forecast_path)
                load_model.assert_not_called()
                np.testing.assert_allclose(result.Prediction_DAM, round(0.1 * scale, 3))
                self.assertEqual(result.Prediction_ID.iloc[0], 0.02)

    def test_every_portfolio_asset_accepts_upward_downward_and_zero_measurements(self):
        configs = [value for value in vars(portfolio).values() if isinstance(value, portfolio.PortfolioIntradayConfig)]
        self.assertEqual(len(configs), 13)  # Incuba has its dedicated derived-baseline runner.
        for config in configs:
            for actual in (0, 0.01, 0.08):
                with self.subTest(asset=config.asset_key, actual=actual):
                    result = portfolio.predict_portfolio_intraday(config, baseline(0.05 / config.baseline_scale), WEATHER, ORIGIN, actual)
                    self.assertEqual(result.Prediction_ID.iloc[0], actual)
                    self.assertEqual(result.Correction_weight.iloc[0], 1)
                    self.assertEqual(result.Correction_weight.iloc[8], 0.5)
                    self.assertTrue(np.isfinite(result.Prediction_ID).all())
                    self.assertTrue(result.Prediction_ID.ge(0).all())

    def test_start_fotovoltaice_production_incident_is_corrected(self):
        config = portfolio.START_FOTOVOLTAICE_INTRADAY_CONFIG
        result = portfolio.predict_portfolio_intraday(config, baseline(0.216 / config.baseline_scale), WEATHER, ORIGIN, 0.015782475754)
        self.assertEqual(result.Prediction_ID.iloc[0], 0.016)
        self.assertEqual(result.Correction.iloc[0], -0.2)

    def test_injection_cap_and_zero_radiation_survive_correction(self):
        config = portfolio.ANASUN_INTRADAY_CONFIG
        weather = WEATHER.copy()
        weather.loc[2:, 'ghi'] = 0
        result = portfolio.predict_portfolio_intraday(config, baseline(1.0), weather, ORIGIN, 3.0)
        self.assertEqual(result.Prediction_ID.iloc[0], 7.5 / 4)
        self.assertTrue(result.Prediction_ID.iloc[2:].eq(0).all())
        np.testing.assert_allclose(result.Correction, result.Prediction_ID - result.Prediction_DAM, atol=0.001)

    def test_default_special_runners_use_saved_forecast_and_never_load_an_old_model(self):
        for module, loader in ((elnet_intraday, 'load_elnet_intraday_bundle'),
                               (horeco_intraday, 'load_horeco_baseline_model'),
                               (hng_intraday, 'load_hng_intraday_bundle')):
            asset = module.__name__.removesuffix('_intraday')
            with self.subTest(asset=asset), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                dam = root / 'dam.xlsx'
                weather = root / 'weather.csv'
                output = root / 'corrected.xlsx'
                baseline(0.1).to_excel(dam, index=False)
                WEATHER.to_csv(weather, index=False)
                readings = [PowerReading(asset, timestamp.isoformat(), 0.08, None, None, 'test')
                            for timestamp in pd.date_range(ORIGIN - pd.Timedelta(minutes=15), ORIGIN, freq='5min')]
                with patch.object(module, loader, side_effect=AssertionError('Model reload must not occur')):
                    result = getattr(module, f'run_{asset}_intraday_forecast')(
                        now=ORIGIN, readings_getter=lambda *a, **kw: readings,
                        weather_path=weather, result_path=output, dam_forecast_path=dam)
                self.assertEqual(result.Prediction_DAM.iloc[0], 0.1)
                self.assertEqual(result.Prediction_ID.iloc[0], 0.02)
                np.testing.assert_allclose(pd.read_excel(output).Prediction_ID, result.Prediction_ID)


if __name__ == '__main__':
    unittest.main()
