from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from hng_intraday import (
    HNG_ID_FEATURES,
    HNG_ID_MODEL_PATH,
    HNGIntradayBundle,
    HNGIntradayInputError,
    build_hng_intraday_features,
    get_latest_hng_forecast_origin,
    load_hng_intraday_bundle,
    predict_hng_intraday,
    run_hng_intraday_forecast,
)
from power_reading.service import PowerReading


ORIGIN = pd.Timestamp("2026-06-01 10:00:00", tz="Europe/Bucharest")


def weather_for_origin(origin: pd.Timestamp = ORIGIN) -> pd.DataFrame:
    targets = pd.date_range(
        origin + pd.Timedelta(minutes=15),
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
            "dewpoint_temp": 8 + sequence / 100,
            "relative_humidity": 45 + sequence / 10,
            "zenith": 30 + sequence / 10,
            "azimuth": 120 + sequence,
        }
    )


class ConstantModel:
    def __init__(self, value: float = 1.5):
        self.value = value

    def predict(self, features):
        return np.full(len(features), self.value)


def fake_bundle(model=None, plant_max_output=None) -> HNGIntradayBundle:
    return HNGIntradayBundle(
        model=model or ConstantModel(),
        feature_columns=HNG_ID_FEATURES,
        plant_max_output=plant_max_output,
        asset="HNG",
        market="Intraday",
        forecast_scope="Every remaining interval of the same delivery day",
    )


class BundleTests(unittest.TestCase):
    def test_real_id_bundle_loads_from_hng_folder(self):
        self.assertTrue(Path(HNG_ID_MODEL_PATH).is_file())
        bundle = load_hng_intraday_bundle()
        self.assertEqual(bundle.asset, "HNG")
        self.assertEqual(bundle.market, "Intraday")
        self.assertEqual(bundle.forecast_scope, "Every remaining interval of the same delivery day")

    def test_real_bundle_feature_order_is_respected_exactly(self):
        bundle = load_hng_intraday_bundle()
        self.assertEqual(bundle.feature_columns, HNG_ID_FEATURES)
        _, features = build_hng_intraday_features(
            weather_for_origin(), ORIGIN, 3.25, bundle.feature_columns
        )
        self.assertEqual(tuple(features.columns), bundle.feature_columns)

    def test_real_bundle_smoke_prediction(self):
        result = predict_hng_intraday(weather_for_origin(), ORIGIN, 3.25)
        self.assertEqual(len(result), 55)
        self.assertTrue(np.isfinite(result["Prediction_ID"]).all())
        self.assertTrue((result["Prediction_ID"] >= 0).all())

    def test_real_bundle_production_orchestration_writes_distinct_id_output(self):
        reading = PowerReading(
            "hng",
            "2026-06-01T07:00:00+00:00",
            3.25,
            None,
            None,
            "test",
        )
        with TemporaryDirectory() as directory:
            weather_path = Path(directory) / "weather.csv"
            result_path = Path(directory) / "hng_id.xlsx"
            weather_for_origin().to_csv(weather_path, index=False)
            result = run_hng_intraday_forecast(
                now=ORIGIN,
                reading_getter=lambda *args, **kwargs: reading,
                weather_path=weather_path,
                result_path=result_path,
            )
            self.assertEqual(len(result), 55)
            self.assertTrue((result["Last_Productie"] == 0.8125).all())
            self.assertTrue((result["Last_Power_MW"] == 3.25).all())
            self.assertTrue(result_path.is_file())
            exported = pd.read_excel(result_path)
            self.assertEqual(list(exported.columns), list(result.columns))
            self.assertTrue((exported["Market"] == "Intraday").all())


class FeatureConstructionTests(unittest.TestCase):
    def test_ten_o_clock_origin_generates_55_same_day_targets(self):
        targets, features = build_hng_intraday_features(weather_for_origin(), ORIGIN, 3.25)
        self.assertEqual(len(targets), 55)
        self.assertEqual(targets[0], pd.Timestamp("2026-06-01 10:15", tz="Europe/Bucharest"))
        self.assertEqual(targets[-1], pd.Timestamp("2026-06-01 23:45", tz="Europe/Bucharest"))
        self.assertTrue((targets.date == ORIGIN.date()).all())
        self.assertEqual(features["Forecast_horizon_minutes"].iloc[0], 15)
        self.assertEqual(features["Forecast_horizon_minutes"].iloc[-1], 825)
        np.testing.assert_array_equal(
            np.diff(features["Forecast_horizon_minutes"].to_numpy()), np.full(54, 15)
        )
        self.assertTrue((features["Last_Productie"] == 3.25).all())

    def test_weather_is_matched_to_each_target_timestamp(self):
        weather = weather_for_origin().sample(frac=1, random_state=42).reset_index(drop=True)
        _, features = build_hng_intraday_features(weather, ORIGIN, 3.25)
        first_target = "2026-06-01T07:15:00Z"
        last_target = "2026-06-01T20:45:00Z"
        first_weather = weather.loc[weather["period_end"] == first_target].iloc[0]
        last_weather = weather.loc[weather["period_end"] == last_target].iloc[0]
        self.assertEqual(features["Temperatura"].iloc[0], first_weather["air_temp"])
        self.assertEqual(features["Azimuth"].iloc[-1], last_weather["azimuth"])

    def test_2345_origin_returns_normal_empty_result(self):
        origin = pd.Timestamp("2026-06-01 23:45", tz="Europe/Bucharest")
        targets, features = build_hng_intraday_features(pd.DataFrame(), origin, 0.0)
        self.assertEqual(len(targets), 0)
        self.assertTrue(features.empty)
        self.assertTrue(predict_hng_intraday(pd.DataFrame(), origin, 0.0, bundle=fake_bundle()).empty)

    def test_missing_target_weather_fails_clearly(self):
        weather = weather_for_origin().iloc[:-1]
        with self.assertRaisesRegex(HNGIntradayInputError, "missing 1 required intervals"):
            build_hng_intraday_features(weather, ORIGIN, 3.25)

    def test_invalid_required_weather_fails_clearly(self):
        weather = weather_for_origin()
        weather.loc[3, "ghi"] = np.nan
        with self.assertRaisesRegex(HNGIntradayInputError, "NaN or infinite"):
            build_hng_intraday_features(weather, ORIGIN, 3.25)

    def test_valid_mixed_iso_weather_timestamps_are_supported(self):
        origin = pd.Timestamp("2026-06-01 02:45", tz="Europe/Bucharest")
        weather = weather_for_origin(origin)
        weather.loc[0, "period_end"] = "2026-06-01"
        targets, features = build_hng_intraday_features(weather, origin, 3.25)
        self.assertEqual(targets[0], pd.Timestamp("2026-06-01 03:00", tz="Europe/Bucharest"))
        self.assertEqual(len(features), len(targets))

    def test_invalid_production_fails_clearly(self):
        with self.assertRaisesRegex(HNGIntradayInputError, "Last_Productie must be finite"):
            build_hng_intraday_features(weather_for_origin(), ORIGIN, np.nan)


class OriginTests(unittest.TestCase):
    def test_latest_valid_reading_at_boundary_supplies_origin(self):
        reading = PowerReading(
            "hng",
            "2026-06-01T07:00:00+00:00",
            3.25,
            None,
            None,
            "test",
        )
        calls = []

        def getter(asset, *, before):
            calls.append((asset, before))
            return reading

        origin, production = get_latest_hng_forecast_origin(now=ORIGIN, reading_getter=getter)
        self.assertEqual(origin, ORIGIN)
        self.assertEqual(production, 3.25)
        self.assertEqual(calls[0][0], "hng")
        self.assertEqual(calls[0][1].tzinfo, timezone.utc)

    def test_missing_or_invalid_live_production_fails_clearly(self):
        with self.assertRaisesRegex(HNGIntradayInputError, "No HNG production measurement"):
            get_latest_hng_forecast_origin(now=ORIGIN, reading_getter=lambda *args, **kwargs: None)

        invalid = PowerReading("hng", "2026-06-01T07:00:00+00:00", np.inf, None, None, "test")
        with self.assertRaisesRegex(HNGIntradayInputError, "must be finite"):
            get_latest_hng_forecast_origin(
                now=ORIGIN,
                reading_getter=lambda *args, **kwargs: invalid,
            )


class PredictionConstraintTests(unittest.TestCase):
    def test_dark_intervals_are_zero_and_predictions_are_clipped_and_capped(self):
        weather = weather_for_origin()
        weather.loc[0, "ghi"] = 0

        class SequenceModel:
            def predict(self, features):
                values = np.full(len(features), 2.0)
                values[0] = 4.0
                values[1] = -2.0
                return values

        bundle = replace(fake_bundle(model=SequenceModel()), plant_max_output=1.75)
        result = predict_hng_intraday(weather, ORIGIN, 3.25, bundle=bundle)
        self.assertEqual(result["Prediction_ID"].iloc[0], 0)
        self.assertEqual(result["Prediction_ID"].iloc[1], 0)
        self.assertTrue((result["Prediction_ID"] <= 1.75).all())

    def test_invalid_model_output_is_rejected(self):
        bundle = fake_bundle(model=ConstantModel(np.nan))
        with self.assertRaisesRegex(Exception, "predictions contain NaN or infinite"):
            predict_hng_intraday(weather_for_origin(), ORIGIN, 3.25, bundle=bundle)


if __name__ == "__main__":
    unittest.main()
