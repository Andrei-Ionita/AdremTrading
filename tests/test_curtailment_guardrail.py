import unittest
from dataclasses import replace
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


class CurtailmentGuardrailTests(unittest.TestCase):
    def test_reported_necaluxan_and_mm_mv_regressions_retain_daylight_baseline(self):
        cases = (
            (portfolio.NECALUXAN_INTRADAY_CONFIG, [3.721, 3.458, 2.822, 2.428, 2.452], 0.2623623189166667),
            (portfolio.MM_MV_INTRADAY_CONFIG, [0.889, 0.787, 0.767, 0.625, 0.523], 0.02766094998653194),
        )
        for config, predictions, actual in cases:
            with self.subTest(asset=config.asset_key):
                frame = baseline(0.0)
                frame.loc[:4, 'Prediction'] = predictions
                result = portfolio.predict_portfolio_intraday(config, frame, WEATHER, ORIGIN, actual)
                np.testing.assert_allclose(result.Prediction_ID, frame.Prediction)
                self.assertTrue(result.Correction.eq(0).all())
                self.assertTrue(result.Correction_weight.eq(0).all())

    def test_all_production_runners_write_guarded_results_without_reloading_models(self):
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
                np.testing.assert_allclose(result.Prediction_ID, round(0.1*scale, 3))
                self.assertTrue(result.Correction_weight.eq(0).all())
                self.assertTrue(result.Correction.eq(0).all())
                write.assert_called_once()

    def test_incuba_threshold_boundary_upward_and_zero_baseline(self):
        for forecast, actual, guarded in ((0.1, 0, True), (0.1, 0.049999, True),
                                          (0.1, 0.05, False), (0.1, 0.06, False),
                                          (0.1, 0.2, False), (0, 0.02, False), (0, 0, False)):
            with self.subTest(forecast=forecast, actual=actual):
                result = incuba.predict_incuba_intraday(baseline(forecast/incuba.ADREM_TO_INCUBA_SCALE), WEATHER, ORIGIN, actual)
                self.assertEqual(result.Prediction_ID.iloc[0], forecast if guarded else actual)
                self.assertEqual(result.Correction_weight.iloc[0], 0 if guarded else 1)

    def test_guard_does_not_disable_injection_caps_or_nighttime_zeros(self):
        weather = WEATHER.copy()
        weather.loc[4:, 'ghi'] = 0
        result = portfolio.predict_portfolio_intraday(portfolio.ANASUN_INTRADAY_CONFIG, baseline(2), weather, ORIGIN, 0)
        self.assertTrue(result.Prediction_ID.iloc[:4].eq(1.875).all())
        self.assertTrue(result.Prediction_ID.iloc[4:].eq(0).all())
        self.assertTrue(result.Correction_weight.eq(0).all())
        np.testing.assert_allclose(result.Correction, result.Prediction_ID-result.Prediction_DAM)
        result = incuba.predict_incuba_intraday(baseline(2), weather, ORIGIN, 0)
        self.assertTrue(result.Prediction_ID.iloc[:4].eq(np.round(incuba.INCUBA_MAX_INTERVAL_ENERGY_MWH, 3)).all())
        self.assertTrue(result.Prediction_ID.iloc[4:].eq(0).all())
        self.assertTrue(result.Correction_weight.eq(0).all())

    def test_zero_forecast_is_not_a_division_error_or_downward_guard(self):
        for actual in (0, 0.1):
            result = portfolio.predict_portfolio_intraday(portfolio.NECALUXAN_INTRADAY_CONFIG, baseline(0), WEATHER, ORIGIN, actual)
            self.assertEqual(result.Prediction_ID.iloc[0], actual)
            self.assertEqual(result.Correction_weight.iloc[0], 1)

    def test_original_threshold_configuration_validation_and_opt_out(self):
        for threshold in (-0.1, 1.1, float('nan'), float('inf')):
            config = replace(portfolio.NECALUXAN_INTRADAY_CONFIG, min_actual_to_forecast_ratio=threshold)
            with self.subTest(threshold=threshold), self.assertRaises(portfolio.PortfolioIntradayInputError):
                portfolio.predict_portfolio_intraday(config, baseline(1), WEATHER, ORIGIN, 0.01)
        config = replace(portfolio.NECALUXAN_INTRADAY_CONFIG, min_actual_to_forecast_ratio=None)
        result = portfolio.predict_portfolio_intraday(config, baseline(1), WEATHER, ORIGIN, 0.01)
        self.assertEqual(result.Prediction_ID.iloc[0], 0.01)


if __name__ == '__main__':
    unittest.main()
