import unittest
from functools import partial
from unittest.mock import patch

import numpy as np
import pandas as pd

import elnet_intraday
import hng_intraday
import horeco_intraday
import incuba_intraday as incuba
import portfolio_intraday as portfolio
from power_reading.service import PowerReading


ORIGIN = pd.Timestamp('2026-10-05 15:45', tz='Europe/Bucharest')
TARGETS = pd.date_range(ORIGIN + pd.Timedelta(minutes=15), ORIGIN.normalize() + pd.Timedelta(hours=23, minutes=45), freq='15min')
WEATHER = pd.DataFrame({'period_end': TARGETS.tz_convert('UTC'), 'ghi': 400.0})


def baseline(value):
    return pd.DataFrame({'Data': TARGETS.tz_localize(None),
                         'Interval': TARGETS.hour * 4 + TARGETS.minute // 15 + 1,
                         'Prediction': value})


class LargeShortfallCorrectionTests(unittest.TestCase):
    def test_necaluxan_and_mm_mv_large_shortfalls_are_corrected(self):
        cases = (
            (portfolio.NECALUXAN_INTRADAY_CONFIG, [3.721, 3.458, 2.822, 2.428, 2.452], 0.2623623189166667),
            (portfolio.MM_MV_INTRADAY_CONFIG, [0.889, 0.787, 0.767, 0.625, 0.523], 0.02766094998653194),
        )
        for config, predictions, actual in cases:
            with self.subTest(asset=config.asset_key):
                frame = baseline(0.0)
                frame.loc[:4, 'Prediction'] = predictions
                result = portfolio.predict_portfolio_intraday(config, frame, WEATHER, ORIGIN, actual)
                weights = np.exp(-np.log(2) * np.arange(len(TARGETS)) * 15 / 120)
                expected = np.maximum(frame.Prediction + weights * (actual - predictions[0]), 0)
                np.testing.assert_allclose(result.Prediction_ID, np.round(expected, 3))
                self.assertEqual(result.Prediction_ID.iloc[0], np.round(actual, 3))
                self.assertEqual(result.Correction_weight.iloc[0], 1)
                self.assertEqual(result.Correction_weight.iloc[8], 0.5)

    def test_all_production_runners_correct_large_shortfalls_without_reloading_models(self):
        special = {'elnet': elnet_intraday.run_elnet_intraday_forecast,
                   'horeco': horeco_intraday.run_horeco_intraday_forecast,
                   'hng': hng_intraday.run_hng_intraday_forecast}
        configs = [value for value in vars(portfolio).values() if isinstance(value, portfolio.PortfolioIntradayConfig)]
        runners = [(config.asset_key, special.get(config.asset_key, partial(portfolio.run_portfolio_intraday_forecast, config)),
                    config.baseline_scale) for config in configs]
        runners.append(('incuba', incuba.run_incuba_intraday_forecast, incuba.ADREM_TO_INCUBA_SCALE))
        for asset, runner, scale in runners:
            readings = [PowerReading(asset, stamp.isoformat(), 0.004, None, None, 'test')
                        for stamp in pd.date_range(ORIGIN-pd.Timedelta(minutes=15), ORIGIN, freq='5min')]
            with self.subTest(asset=asset), patch('joblib.load', side_effect=AssertionError('No older model')), patch(
                'pathlib.Path.is_file', return_value=True
            ), patch('pathlib.Path.mkdir'), patch('pandas.read_excel', return_value=baseline(0.1)), patch(
                'pandas.read_csv', return_value=WEATHER
            ), patch.object(pd.DataFrame, 'to_excel') as write:
                result = runner(now=ORIGIN, readings_getter=lambda *args, **kwargs: readings)
                weights = np.exp(-np.log(2) * np.arange(len(TARGETS)) * 15 / 120)
                expected = 0.1 * scale + weights * (0.001 - 0.1 * scale)
                np.testing.assert_allclose(result.Prediction_ID, np.round(expected, 3))
                self.assertEqual(result.Prediction_ID.iloc[0], 0.001)
                self.assertEqual(result.Correction_weight.iloc[0], 1)
                self.assertEqual(result.Correction_weight.iloc[8], 0.5)
                self.assertTrue(result.Correction.lt(0).all())
                write.assert_called_once()

    def test_incuba_large_shortfall_former_boundary_upward_and_zero_baseline(self):
        for forecast, actual in ((0.1, 0), (0.1, 0.01), (0.1, 0.049999),
                                (0.1, 0.05), (0.1, 0.06),
                                (0.1, 0.2), (0, 0.02), (0, 0)):
            with self.subTest(forecast=forecast, actual=actual):
                result = incuba.predict_incuba_intraday(baseline(forecast/incuba.ADREM_TO_INCUBA_SCALE), WEATHER, ORIGIN, actual)
                self.assertEqual(result.Prediction_ID.iloc[0], np.round(actual, 3))
                self.assertEqual(result.Correction_weight.iloc[0], 1)
                self.assertEqual(result.Correction_weight.iloc[8], 0.5)

    def test_correction_preserves_injection_caps_and_nighttime_zeros(self):
        weather = WEATHER.copy()
        weather.loc[4:, 'ghi'] = 0
        result = portfolio.predict_portfolio_intraday(portfolio.ANASUN_INTRADAY_CONFIG, baseline(2), weather, ORIGIN, 3)
        self.assertTrue(result.Prediction_ID.iloc[:4].eq(1.875).all())
        self.assertTrue(result.Prediction_ID.iloc[4:].eq(0).all())
        self.assertEqual(result.Correction_weight.iloc[0], 1)
        np.testing.assert_allclose(result.Correction, result.Prediction_ID-result.Prediction_DAM)
        result = incuba.predict_incuba_intraday(baseline(2), weather, ORIGIN, 3)
        self.assertTrue(result.Prediction_ID.iloc[:4].eq(np.round(incuba.INCUBA_MAX_INTERVAL_ENERGY_MWH, 3)).all())
        self.assertTrue(result.Prediction_ID.iloc[4:].eq(0).all())
        self.assertEqual(result.Correction_weight.iloc[0], 1)

    def test_zero_forecast_can_be_corrected(self):
        for actual in (0, 0.1):
            result = portfolio.predict_portfolio_intraday(portfolio.NECALUXAN_INTRADAY_CONFIG, baseline(0), WEATHER, ORIGIN, actual)
            self.assertEqual(result.Prediction_ID.iloc[0], actual)
            self.assertEqual(result.Correction_weight.iloc[0], 1)

    def test_invalid_production_is_still_rejected(self):
        for actual in (-0.1, float('nan'), float('inf')):
            with self.subTest(actual=actual):
                with self.assertRaises(portfolio.PortfolioIntradayInputError):
                    portfolio.predict_portfolio_intraday(
                        portfolio.NECALUXAN_INTRADAY_CONFIG, baseline(1), WEATHER, ORIGIN, actual
                    )
                with self.assertRaises(incuba.IncubaIntradayInputError):
                    incuba.predict_incuba_intraday(baseline(1), WEATHER, ORIGIN, actual)


if __name__ == '__main__':
    unittest.main()
