from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from horeco_intraday import (
    HORECO_BASELINE_FEATURES,
    HORECO_ID_MODEL_PATH,
    HorecoBaselineModel,
    HorecoIntradayInputError,
    build_horeco_baseline_features,
    get_latest_horeco_forecast_origin,
    load_horeco_baseline_model,
    predict_horeco_intraday,
    run_horeco_intraday_forecast,
)
from power_reading.service import PowerReading


ORIGIN = pd.Timestamp("2026-07-29 10:00", tz="Europe/Bucharest")


def weather_for_origin(origin: pd.Timestamp = ORIGIN) -> pd.DataFrame:
    targets = pd.date_range(
        origin,
        origin.normalize() + pd.Timedelta(hours=23, minutes=45),
        freq="15min",
        tz="Europe/Bucharest",
    )
    sequence = np.arange(len(targets), dtype=float)
    return pd.DataFrame(
        {
            "period_end": targets.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
            "air_temp": 20 + sequence / 100,
            "cloud_opacity": 10 + sequence,
            "ghi": np.maximum(500 - sequence * 5, 0),
        }
    )


class ConstantModel:
    n_features_in_ = len(HORECO_BASELINE_FEATURES)

    def __init__(self, value: float = 1.0):
        self.value = value

    def predict(self, features):
        return np.full(len(features), self.value)


def fake_model(value: float = 1.0) -> HorecoBaselineModel:
    return HorecoBaselineModel(model=ConstantModel(value))


class ModelTests(unittest.TestCase):
    def test_current_horeco_model_loads(self):
        self.assertTrue(Path(HORECO_ID_MODEL_PATH).is_file())
        loaded = load_horeco_baseline_model()
        self.assertEqual(loaded.feature_columns, HORECO_BASELINE_FEATURES)

    def test_real_model_smoke_prediction(self):
        result = predict_horeco_intraday(weather_for_origin(), ORIGIN, 1.0)
        self.assertEqual(len(result), 55)
        self.assertTrue(np.isfinite(result["Prediction_ID"]).all())
        self.assertTrue((result["Prediction_ID"] >= 0).all())


class OriginTests(unittest.TestCase):
    def test_latest_horeco_reading_supplies_forecast_origin(self):
        reading = PowerReading(
            "horeco",
            "2026-07-29T07:00:00+00:00",
            1.25,
            None,
            None,
            "test",
        )
        calls = []

        def getter(asset, *, before):
            calls.append((asset, before))
            return reading

        origin, production = get_latest_horeco_forecast_origin(
            now=ORIGIN, reading_getter=getter
        )
        self.assertEqual(origin, ORIGIN)
        self.assertEqual(production, 0.3125)
        self.assertEqual(calls[0][0], "horeco")

    def test_previous_day_reading_is_rejected(self):
        reading = PowerReading(
            "horeco",
            "2026-07-28T20:45:00+00:00",
            0.0,
            None,
            None,
            "test",
        )
        with self.assertRaisesRegex(HorecoIntradayInputError, "current delivery day"):
            get_latest_horeco_forecast_origin(
                now=ORIGIN, reading_getter=lambda *args, **kwargs: reading
            )


class ForecastTests(unittest.TestCase):
    def test_features_match_weather_by_timestamp(self):
        weather = weather_for_origin().sample(frac=1, random_state=7).reset_index(drop=True)
        targets, features = build_horeco_baseline_features(weather, ORIGIN)
        self.assertEqual(targets[0], ORIGIN)
        self.assertEqual(tuple(features.columns), HORECO_BASELINE_FEATURES)
        first_weather = weather.loc[
            weather["period_end"] == "2026-07-29T07:00:00Z"
        ].iloc[0]
        self.assertEqual(features["Temperatura"].iloc[0], first_weather["air_temp"])

    def test_latest_actual_anchors_and_then_decays_toward_baseline(self):
        result = predict_horeco_intraday(
            weather_for_origin(), ORIGIN, 0.5, baseline_model=fake_model(0.25)
        )
        self.assertGreater(result["Prediction_ID"].iloc[0], 0.45)
        self.assertGreater(result["Prediction_ID"].iloc[0], result["Prediction_ID"].iloc[-1])
        self.assertTrue((result["Baseline_prediction"] == 0.25).all())
        self.assertTrue((result["Last_Productie"] == 0.5).all())
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[0], 15)

    def test_dark_intervals_are_zero(self):
        weather = weather_for_origin()
        weather.loc[1, "ghi"] = 0
        result = predict_horeco_intraday(
            weather, ORIGIN, 0.5, baseline_model=fake_model(0.25)
        )
        self.assertEqual(result["Prediction_ID"].iloc[0], 0)

    def test_end_of_day_returns_empty_result(self):
        origin = pd.Timestamp("2026-07-29 23:45", tz="Europe/Bucharest")
        result = predict_horeco_intraday(
            pd.DataFrame(), origin, 0.0, baseline_model=fake_model()
        )
        self.assertTrue(result.empty)

    def test_orchestration_writes_distinct_intraday_file(self):
        reading = PowerReading(
            "horeco",
            "2026-07-29T07:00:00+00:00",
            1.25,
            None,
            None,
            "test",
        )
        with TemporaryDirectory() as directory:
            weather_path = Path(directory) / "weather.csv"
            result_path = Path(directory) / "horeco_id.xlsx"
            model_path = Path(directory) / "model.joblib"
            weather_for_origin().to_csv(weather_path, index=False)
            import joblib

            joblib.dump(ConstantModel(), model_path)
            result = run_horeco_intraday_forecast(
                now=ORIGIN,
                reading_getter=lambda *args, **kwargs: reading,
                model_path=model_path,
                weather_path=weather_path,
                result_path=result_path,
            )
            self.assertEqual(len(result), 55)
            self.assertTrue((result["Last_Productie"] == 0.3125).all())
            self.assertTrue(result_path.is_file())
            exported = pd.read_excel(result_path)
            self.assertEqual(list(exported.columns), list(result.columns))


if __name__ == "__main__":
    unittest.main()
