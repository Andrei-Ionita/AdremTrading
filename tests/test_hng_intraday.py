from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from hng_intraday import (
    CORRECTION_HALF_LIFE_MINUTES,
    HNG_DAM_FEATURES,
    HNG_DAM_MODEL_PATH,
    HNGIntradayBundle,
    HNGIntradayInputError,
    build_hng_intraday_features,
    calculate_hng_interval_energy,
    get_latest_hng_forecast_origin,
    load_hng_intraday_bundle,
    predict_hng_intraday,
    run_hng_intraday_forecast,
)
from power_reading.service import PowerReading


ORIGIN = pd.Timestamp("2026-06-01 10:00:00", tz="Europe/Bucharest")


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
        feature_columns=HNG_DAM_FEATURES,
        plant_max_output=plant_max_output,
        asset="HNG",
        market="Intraday",
        forecast_scope="Every remaining interval of the same delivery day",
    )


def production_readings(
    values=(3.25, 3.25, 3.25, 3.25),
    timestamps=("09:45", "09:50", "09:55", "10:00"),
):
    return [
        PowerReading(
            "hng",
            pd.Timestamp(f"2026-06-01 {timestamp}", tz="Europe/Bucharest")
            .tz_convert("UTC")
            .isoformat(),
            value,
            None,
            None,
            "test",
        )
        for timestamp, value in zip(timestamps, values)
    ]


class BundleTests(unittest.TestCase):
    def test_original_dam_model_loads_for_intraday_correction(self):
        self.assertTrue(Path(HNG_DAM_MODEL_PATH).is_file())
        self.assertEqual(Path(HNG_DAM_MODEL_PATH).name, "rs_xgb_hng_prod_15min_0626.pkl")
        bundle = load_hng_intraday_bundle()
        self.assertEqual(bundle.asset, "HNG")
        self.assertEqual(bundle.market, "Intraday")
        self.assertEqual(bundle.forecast_scope, "Every remaining interval of the same delivery day")
        self.assertEqual(bundle.model.n_features_in_, 6)

    def test_real_bundle_feature_order_is_respected_exactly(self):
        bundle = load_hng_intraday_bundle()
        self.assertEqual(bundle.feature_columns, HNG_DAM_FEATURES)
        _, features = build_hng_intraday_features(
            weather_for_origin(), ORIGIN, bundle.feature_columns
        )
        self.assertEqual(tuple(features.columns), bundle.feature_columns)

    def test_real_bundle_smoke_prediction(self):
        result = predict_hng_intraday(weather_for_origin(), ORIGIN, 3.25)
        self.assertEqual(len(result), 55)
        self.assertTrue(np.isfinite(result["Prediction_ID"]).all())
        self.assertTrue((result["Prediction_ID"] >= 0).all())

    def test_real_bundle_production_orchestration_writes_distinct_id_output(self):
        with TemporaryDirectory() as directory:
            weather_path = Path(directory) / "weather.csv"
            result_path = Path(directory) / "hng_id.xlsx"
            weather_for_origin().to_csv(weather_path, index=False)
            result = run_hng_intraday_forecast(
                now=ORIGIN,
                readings_getter=lambda *args, **kwargs: production_readings(),
                weather_path=weather_path,
                result_path=result_path,
            )
            self.assertEqual(len(result), 55)
            self.assertTrue((result["Last_Productie"] == 0.8125).all())
            self.assertNotIn("Last_Power_MW", result.columns)
            self.assertTrue(result_path.is_file())
            exported = pd.read_excel(result_path)
            self.assertEqual(list(exported.columns), list(result.columns))
            self.assertTrue((exported["Market"] == "Intraday").all())


class FeatureConstructionTests(unittest.TestCase):
    def test_ten_o_clock_origin_generates_55_same_day_targets(self):
        targets, features = build_hng_intraday_features(weather_for_origin(), ORIGIN)
        self.assertEqual(len(targets), 55)
        self.assertEqual(targets[0], pd.Timestamp("2026-06-01 10:15", tz="Europe/Bucharest"))
        self.assertEqual(targets[-1], pd.Timestamp("2026-06-01 23:45", tz="Europe/Bucharest"))
        self.assertTrue((targets.date == ORIGIN.date()).all())
        self.assertEqual(tuple(features.columns), HNG_DAM_FEATURES)
        self.assertNotIn("Last_Productie", features.columns)

        result = predict_hng_intraday(
            weather_for_origin(), ORIGIN, 3.25, bundle=fake_bundle()
        )
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[0], 15)
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[-1], 825)
        np.testing.assert_array_equal(
            np.diff(result["Forecast_horizon_minutes"].to_numpy()), np.full(54, 15)
        )

    def test_weather_is_matched_to_each_target_timestamp(self):
        weather = weather_for_origin().sample(frac=1, random_state=42).reset_index(drop=True)
        _, features = build_hng_intraday_features(weather, ORIGIN)
        first_target = "2026-06-01T07:15:00Z"
        first_weather = weather.loc[weather["period_end"] == first_target].iloc[0]
        self.assertEqual(features["Temperatura"].iloc[0], first_weather["air_temp"])
        self.assertEqual(features["Radiatie"].iloc[0], first_weather["ghi"])

    def test_2345_origin_returns_normal_empty_result(self):
        origin = pd.Timestamp("2026-06-01 23:45", tz="Europe/Bucharest")
        targets, features = build_hng_intraday_features(pd.DataFrame(), origin)
        self.assertEqual(len(targets), 0)
        self.assertTrue(features.empty)
        self.assertTrue(predict_hng_intraday(pd.DataFrame(), origin, 0.0, bundle=fake_bundle()).empty)

    def test_missing_target_weather_fails_clearly(self):
        weather = weather_for_origin().iloc[:-1]
        with self.assertRaisesRegex(HNGIntradayInputError, "missing 1 required intervals"):
            build_hng_intraday_features(weather, ORIGIN)

    def test_invalid_required_weather_fails_clearly(self):
        weather = weather_for_origin()
        weather.loc[3, "ghi"] = np.nan
        with self.assertRaisesRegex(HNGIntradayInputError, "NaN or infinite"):
            build_hng_intraday_features(weather, ORIGIN)

    def test_valid_mixed_iso_weather_timestamps_are_supported(self):
        origin = pd.Timestamp("2026-06-01 02:45", tz="Europe/Bucharest")
        weather = weather_for_origin(origin)
        weather.loc[0, "period_end"] = "2026-05-31 23:45:00+00:00"
        targets, features = build_hng_intraday_features(weather, origin)
        self.assertEqual(targets[0], pd.Timestamp("2026-06-01 03:00", tz="Europe/Bucharest"))
        self.assertEqual(len(features), len(targets))

    def test_invalid_actual_energy_fails_clearly(self):
        with self.assertRaisesRegex(HNGIntradayInputError, "Last_Productie must be finite"):
            predict_hng_intraday(
                weather_for_origin(), ORIGIN, np.nan, bundle=fake_bundle()
            )

class IntervalEnergyTests(unittest.TestCase):
    def test_completed_interval_uses_trapezoidal_energy_integration(self):
        energy = calculate_hng_interval_energy(
            production_readings(values=(2.0, 4.0, 6.0, 8.0)),
            ORIGIN - pd.Timedelta(minutes=15),
            ORIGIN,
        )
        self.assertAlmostEqual(energy, 1.25)
        self.assertNotAlmostEqual(energy, 8.0 * 0.25)

    def test_irregular_samples_are_integrated_to_exact_interval_boundaries(self):
        readings = production_readings(
            values=(2.0, 4.0, 8.0),
            timestamps=("09:44", "09:50", "09:57"),
        )
        energy = calculate_hng_interval_energy(
            readings,
            ORIGIN - pd.Timedelta(minutes=15),
            ORIGIN,
        )
        self.assertAlmostEqual(energy, 1.35)

    def test_stale_boundary_or_large_sample_gap_fails_clearly(self):
        stale = production_readings(
            values=(2.0, 4.0),
            timestamps=("09:39", "09:59"),
        )
        with self.assertRaisesRegex(HNGIntradayInputError, "interval start is too old"):
            calculate_hng_interval_energy(
                stale,
                ORIGIN - pd.Timedelta(minutes=15),
                ORIGIN,
            )

        gap = production_readings(
            values=(2.0, 4.0, 6.0),
            timestamps=("09:45", "09:54", "10:00"),
        )
        with self.assertRaisesRegex(HNGIntradayInputError, "gap larger than 7.5 minutes"):
            calculate_hng_interval_energy(
                gap,
                ORIGIN - pd.Timedelta(minutes=15),
                ORIGIN,
            )

    def test_sample_after_interval_end_is_rejected(self):
        readings = production_readings(timestamps=("09:45", "09:50", "09:55", "10:01"))
        with self.assertRaisesRegex(HNGIntradayInputError, "after the interval end"):
            calculate_hng_interval_energy(
                readings,
                ORIGIN - pd.Timedelta(minutes=15),
                ORIGIN,
            )


class OriginTests(unittest.TestCase):
    def test_completed_interval_energy_supplies_origin(self):
        calls = []

        def getter(asset, *, start, end):
            calls.append((asset, start, end))
            return production_readings()

        origin, production = get_latest_hng_forecast_origin(now=ORIGIN, readings_getter=getter)
        self.assertEqual(origin, ORIGIN)
        self.assertEqual(production, 0.8125)
        self.assertEqual(calls[0][0], "hng")
        self.assertEqual(calls[0][1].tzinfo, timezone.utc)
        self.assertEqual(calls[0][2].tzinfo, timezone.utc)

    def test_missing_or_invalid_live_production_fails_clearly(self):
        with self.assertRaisesRegex(HNGIntradayInputError, "No HNG power samples"):
            get_latest_hng_forecast_origin(
                now=ORIGIN,
                readings_getter=lambda *args, **kwargs: [],
            )

        invalid = production_readings()
        invalid[2] = PowerReading(
            "hng", invalid[2].timestamp_utc, np.inf, None, None, "test"
        )
        with self.assertRaisesRegex(HNGIntradayInputError, "must be finite"):
            get_latest_hng_forecast_origin(
                now=ORIGIN,
                readings_getter=lambda *args, **kwargs: invalid,
            )


class PredictionConstraintTests(unittest.TestCase):
    def test_actual_residual_is_strong_first_and_decays_with_sixty_minute_half_life(self):
        result = predict_hng_intraday(
            weather_for_origin(), ORIGIN, 1.4, bundle=fake_bundle(model=ConstantModel(1.0))
        )
        self.assertEqual(CORRECTION_HALF_LIFE_MINUTES, 60.0)
        self.assertEqual(result["Reference_DAM_Prediction"].iloc[0], 1.0)
        self.assertEqual(result["Actual_minus_DAM"].iloc[0], 0.4)
        self.assertEqual(result["Correction_weight"].iloc[0], 1.0)
        self.assertEqual(result["Correction"].iloc[0], 0.4)
        self.assertEqual(result["Prediction_ID"].iloc[0], 1.4)
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[4], 75)
        self.assertEqual(result["Correction_weight"].iloc[4], 0.5)
        self.assertEqual(result["Correction"].iloc[4], 0.2)
        self.assertEqual(result["Prediction_ID"].iloc[4], 1.2)

    def test_dark_intervals_are_zero_and_predictions_are_clipped_and_capped(self):
        weather = weather_for_origin()
        weather.loc[1, "ghi"] = 0

        class SequenceModel:
            def predict(self, features):
                values = np.full(len(features), 2.0)
                values[0] = 4.0
                if len(values) > 1:
                    values[1] = -2.0
                return values

        bundle = replace(fake_bundle(model=SequenceModel()), plant_max_output=1.75)
        result = predict_hng_intraday(weather, ORIGIN, 3.25, bundle=bundle)
        self.assertEqual(result["Prediction_ID"].iloc[0], 0)
        self.assertEqual(result["Prediction_DAM"].iloc[1], 0)
        self.assertGreater(result["Prediction_ID"].iloc[1], 0)
        self.assertTrue((result["Prediction_ID"] <= 1.75).all())

    def test_invalid_model_output_is_rejected(self):
        bundle = fake_bundle(model=ConstantModel(np.nan))
        with self.assertRaisesRegex(Exception, "predictions contain NaN or infinite"):
            predict_hng_intraday(weather_for_origin(), ORIGIN, 3.25, bundle=bundle)


if __name__ == "__main__":
    unittest.main()
