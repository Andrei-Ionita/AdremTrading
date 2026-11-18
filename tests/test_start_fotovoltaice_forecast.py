import inspect
import os
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import joblib
import numpy as np
import pandas as pd
import requests

from ml import fetching_Start_Fotovoltaice_data_15min as fetch
from ml import predicting_exporting_Start_Fotovoltaice_15min as predict
from forecast_time import delivery_intervals, delivery_times, utc_strings
from portfolio_intraday import START_FOTOVOLTAICE_INTRADAY_CONFIG as CONFIG
from portfolio_intraday import run_portfolio_intraday_forecast
from portfolio_export import ASSET_FILES


def weather(times=None):
    return pd.DataFrame({
        'period_end': times if times is not None else ['2026-01-15T10:00:00Z', '2026-01-15T10:15:00Z'],
        'air_temp': [20., 19.], 'cloud_opacity': [30., 40.], 'ghi': [100., 0.],
    })


class StartFotovoltaiceForecastTests(unittest.TestCase):
    def test_generated_dst_workbook_flows_through_intraday_correction(self):
        for origin_utc, day_quarters in (
            ("2026-03-29 00:45Z", 92),
            ("2026-10-25 00:15Z", 100),
            ("2026-10-25 01:15Z", 100),
            ("2026-10-25 10:00Z", 100),
        ):
            with self.subTest(origin=origin_utc), tempfile.TemporaryDirectory() as tmp:
                origin = pd.Timestamp(origin_utc).tz_convert("Europe/Bucharest")
                times = pd.date_range(
                    origin.normalize(), origin.normalize() + pd.DateOffset(days=1),
                    freq="15min", inclusive="left",
                )
                data = pd.DataFrame({
                    "period_end": utc_strings(times), "air_temp": 20.0,
                    "cloud_opacity": 10.0, "ghi": 100.0,
                })
                data.loc[len(data) - 1, "ghi"] = 0.0
                root = Path(tmp)
                config = replace(CONFIG, weather_path=root / "weather.csv",
                                 dam_results_path=root / "dam.xlsx",
                                 intraday_results_path=root / "corrected.xlsx")
                data.to_csv(config.weather_path, index=False)
                model = Mock(n_features_in_=6)
                model.predict.side_effect = lambda features: np.full(len(features), 0.1)
                with patch("ml.joblib.load", return_value=model):
                    generated = predict(1, 24, 0, weather_path=config.weather_path,
                                        output_path=config.dam_results_path)
                self.assertEqual(len(generated), day_quarters)
                readings = [SimpleNamespace(timestamp_utc=stamp.isoformat(), pv_mw=0.32)
                            for stamp in pd.date_range(origin.tz_convert("UTC") - pd.Timedelta(minutes=15),
                                                       origin.tz_convert("UTC"), freq="5min")]
                result = run_portfolio_intraday_forecast(
                    config, now=origin, readings_getter=lambda *args, **kwargs: readings,
                )
                targets = times[times > origin]
                pd.testing.assert_index_equal(delivery_times(result), targets, check_names=False)
                self.assertEqual(result.Interval.tolist(), delivery_intervals(targets).tolist())
                self.assertFalse(result.Data_UTC.duplicated().any())
                np.testing.assert_allclose(result.Last_Productie, 0.08)
                self.assertEqual(result.Prediction_ID.iloc[0], 0.08)
                self.assertEqual(result.Correction_weight.iloc[0], 1.0)
                self.assertEqual(result.Correction_weight.iloc[8], 0.5)
                self.assertEqual(result.Prediction_ID.iloc[8], 0.09)
                self.assertEqual(result.Prediction_ID.iloc[-1], 0.0)
                self.assertTrue(result.Prediction_ID.between(0, CONFIG.max_interval_energy_mwh).all())
                saved = pd.read_excel(config.intraday_results_path)
                self.assertEqual(saved.Interval.tolist(), result.Interval.tolist())
                self.assertEqual(saved.Data_UTC.tolist(), result.Data_UTC.tolist())

    def run_prediction(self, data, model, limitation=0):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp)/'weather.csv', Path(tmp)/'forecast.xlsx'
            data.to_csv(source, index=False)
            with patch('ml.joblib.load', return_value=model) as load:
                result = predict(1, 24, limitation, weather_path=source, output_path=target)
            self.assertEqual(load.call_args.args[0], inspect.signature(predict).parameters['model_path'].default)
            reread = pd.read_excel(target)
            np.testing.assert_allclose(reread.Prediction, result.Prediction)
            self.assertEqual(reread.Data_UTC.tolist(), result.Data_UTC.tolist())
            return result

    def test_configuration_and_export_use_own_files(self):
        self.assertEqual(CONFIG.baseline_scale, 1)
        self.assertEqual(CONFIG.dam_results_path.parent.name, 'Start Fotovoltaice')
        self.assertEqual(CONFIG.weather_path.name, 'Borcea_15min.csv')
        self.assertNotIn('Derived', CONFIG.intraday_results_path.name)
        self.assertIn(('Start_Fotovoltaice', 'Start Fotovoltaice', 'Start_Fotovoltaice', 'Borcea'), ASSET_FILES)

    def test_model_loads_and_predicts_supplied_weather(self):
        model_path = inspect.signature(predict).parameters['model_path'].default
        model = joblib.load(model_path)
        self.assertEqual(model.n_features_in_, 6)
        with tempfile.TemporaryDirectory() as tmp:
            result = predict(1, 24, 0, output_path=Path(tmp)/'forecast.xlsx')
        self.assertGreater(len(result), 96)
        self.assertTrue(np.isfinite(result.Prediction).all())
        self.assertTrue(result.Prediction.between(0, CONFIG.max_interval_energy_mwh).all())
        self.assertFalse(result.Data_UTC.duplicated().any())
        self.assertGreater(result.Prediction.max(), 0)

    def test_expected_features_and_winter_time(self):
        model = Mock(n_features_in_=6)
        model.predict.return_value = np.array([.125, .2])
        result = self.run_prediction(weather(), model)
        np.testing.assert_array_equal(model.predict.call_args.args[0], [
            [49., 20., 30., 100., 1., 0.], [50., 19., 40., 0., 1., 1.],
        ])
        self.assertEqual(result.Data.iloc[0], pd.Timestamp('2026-01-15 12:00'))
        self.assertEqual(result.Prediction.tolist(), [.125, 0])

    def test_summer_time(self):
        model = Mock(n_features_in_=6)
        model.predict.return_value = np.array([.1, .1])
        result = self.run_prediction(weather(['2026-07-15T10:00:00Z','2026-07-15T10:15:00Z']), model)
        self.assertEqual(result.Data.iloc[0], pd.Timestamp('2026-07-15 13:00'))
        self.assertEqual(model.predict.call_args.args[0][0, 0], 53)

    def test_repeated_dst_hour_keeps_unique_delivery_intervals(self):
        model = Mock(n_features_in_=6)
        model.predict.return_value = np.array([.1, .1])
        result = self.run_prediction(weather(['2026-10-25T00:00:00Z','2026-10-25T01:00:00Z']), model)
        self.assertEqual(result.Data.iloc[0], result.Data.iloc[1])
        self.assertEqual(result.Interval.tolist(), [13, 17])
        self.assertFalse(result.Lookup.duplicated().any())

    def test_capacity_and_nonnegative_bounds(self):
        for values, expected in (([1., 1.], [.249, .249]), ([-.1, -.2], [0., 0.])):
            model = Mock(n_features_in_=6)
            model.predict.return_value = np.array(values)
            result = self.run_prediction(weather().assign(ghi=100.), model)
            self.assertEqual(result.Prediction.tolist(), expected)

    def test_limitation_is_applied_when_requested(self):
        model = Mock(n_features_in_=6)
        model.predict.return_value = np.array([.2, .2])
        result = self.run_prediction(weather().assign(ghi=100.), model, limitation=25)
        self.assertEqual(result.Prediction.tolist(), [.15, .15])

    def test_invalid_model_output_and_features_fail(self):
        for model, data in (
            (Mock(n_features_in_=5), weather()),
            (Mock(n_features_in_=6, predict=Mock(return_value=np.array([np.nan, .1]))), weather()),
            (Mock(n_features_in_=6, predict=Mock(return_value=np.array([.1]))), weather()),
            (Mock(n_features_in_=6), weather().assign(ghi=np.inf)),
            (Mock(n_features_in_=6), weather().drop(columns='ghi')),
            (Mock(n_features_in_=6), weather(['2026-01-15T10:00Z','2026-01-15T10:00Z'])),
        ):
            with self.subTest(model=model, columns=list(data)), self.assertRaises(ValueError):
                self.run_prediction(data, model)

    def test_fetch_uses_configured_coordinates_and_utc(self):
        response = Mock(status_code=200, content=weather().to_csv(index=False).encode())
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'START_FOTOVOLTAICE_LATITUDE': '44.0', 'START_FOTOVOLTAICE_LONGITUDE': '27.0',
        }), patch('ml.requests.get', return_value=response) as get:
            fetch(Path(tmp)/'Solcast'/'Borcea_15min.csv')
            params = get.call_args.kwargs['params']
            self.assertEqual((params['latitude'], params['longitude']), (44., 27.))
            self.assertEqual(params['time_zone'], 0)
            self.assertEqual(params['period'], 'PT15M')
            self.assertEqual(get.call_args.kwargs['timeout'], 60)

    def test_fetch_defaults_to_confirmed_borcea_coordinates(self):
        response = Mock(status_code=200, content=weather().to_csv(index=False).encode())
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), patch(
            'ml.requests.get', return_value=response
        ) as get:
            fetch(Path(tmp)/'weather.csv')
            params = get.call_args.kwargs['params']
            self.assertEqual((params['latitude'], params['longitude']), (44.331569, 27.746911))

    def test_invalid_coordinates_fail_without_network(self):
        with patch.dict(os.environ, {'START_FOTOVOLTAICE_LATITUDE': 'invalid'}), patch('ml.requests.get') as get:
            with self.assertRaisesRegex(ValueError, 'START_FOTOVOLTAICE_LATITUDE'):
                fetch()
            get.assert_not_called()

    def test_failed_weather_fetch_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'weather.csv'
            target.write_bytes(b'existing weather')
            with patch('ml.requests.get', return_value=Mock(status_code=401)):
                with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
                    fetch(target, latitude=44., longitude=27.)
            self.assertEqual(target.read_bytes(), b'existing weather')

    def test_weather_network_errors_do_not_expose_api_key(self):
        with patch('ml.requests.get', side_effect=requests.Timeout('api_key=test-secret')):
            with self.assertRaises(RuntimeError) as caught:
                fetch(latitude=44., longitude=27.)
        self.assertNotIn('test-secret', str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)


if __name__ == '__main__':
    unittest.main()
