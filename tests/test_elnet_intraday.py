from __future__ import annotations

import unittest
from datetime import timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from elnet_intraday import (
    CORRECTION_HALF_LIFE_MINUTES,
    ELNET_DAM_FEATURES,
    ELNET_DAM_MODEL_PATH,
    ElnetIntradayBundle,
    ElnetIntradayInputError,
    build_elnet_intraday_features,
    calculate_elnet_interval_energy,
    get_latest_elnet_forecast_origin,
    load_elnet_intraday_bundle,
    predict_elnet_intraday,
    run_elnet_intraday_forecast,
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
        }
    )


class ConstantModel:
    def __init__(self, value: float = 1.0):
        self.value = value

    def predict(self, features):
        return np.full(len(features), self.value)


def fake_bundle(model=None) -> ElnetIntradayBundle:
    return ElnetIntradayBundle(
        model=model or ConstantModel(),
        feature_columns=ELNET_DAM_FEATURES,
        plant_max_output=None,
        asset="Elnet",
        market="Intraday",
        forecast_scope="Every remaining interval of the same delivery day",
    )


def production_readings(
    values=(3.25, 3.25, 3.25, 3.25),
    timestamps=("09:45", "09:50", "09:55", "10:00"),
):
    return [
        PowerReading(
            "elnet",
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


class ElnetModelTests(unittest.TestCase):
    def test_existing_dam_model_loads_with_exact_feature_order(self):
        self.assertTrue(Path(ELNET_DAM_MODEL_PATH).is_file())
        bundle = load_elnet_intraday_bundle()
        self.assertEqual(bundle.asset, "Elnet")
        self.assertEqual(bundle.feature_columns, ELNET_DAM_FEATURES)
        self.assertEqual(bundle.model.n_features_in_, 6)

        _, features = build_elnet_intraday_features(
            weather_for_origin(), ORIGIN, bundle.feature_columns
        )
        self.assertEqual(tuple(features.columns), ELNET_DAM_FEATURES)

    def test_real_model_smoke_prediction(self):
        result = predict_elnet_intraday(weather_for_origin(), ORIGIN, 0.5)
        self.assertEqual(len(result), 55)
        self.assertTrue(np.isfinite(result["Prediction_ID"]).all())
        self.assertTrue((result["Prediction_ID"] >= 0).all())


class ElnetFeatureTests(unittest.TestCase):
    def test_origin_generates_only_remaining_same_day_targets(self):
        targets, features = build_elnet_intraday_features(weather_for_origin(), ORIGIN)
        self.assertEqual(len(targets), 55)
        self.assertEqual(targets[0], pd.Timestamp("2026-06-01 10:15", tz="Europe/Bucharest"))
        self.assertEqual(targets[-1], pd.Timestamp("2026-06-01 23:45", tz="Europe/Bucharest"))
        self.assertTrue((targets.date == ORIGIN.date()).all())
        self.assertEqual(tuple(features.columns), ELNET_DAM_FEATURES)

        result = predict_elnet_intraday(
            weather_for_origin(), ORIGIN, 0.5, bundle=fake_bundle()
        )
        np.testing.assert_array_equal(
            result["Forecast_horizon_minutes"].to_numpy(),
            np.arange(15, 826, 15),
        )

    def test_weather_is_matched_to_target_timestamp(self):
        weather = weather_for_origin().sample(frac=1, random_state=7).reset_index(drop=True)
        _, features = build_elnet_intraday_features(weather, ORIGIN)
        expected = weather.loc[weather["period_end"] == "2026-06-01T07:15:00Z"].iloc[0]
        self.assertEqual(features["Temperatura"].iloc[0], expected["air_temp"])
        self.assertEqual(features["Radiatie"].iloc[0], expected["ghi"])

    def test_missing_or_invalid_weather_fails_clearly(self):
        with self.assertRaisesRegex(ElnetIntradayInputError, "missing 1 required intervals"):
            build_elnet_intraday_features(weather_for_origin().iloc[:-1], ORIGIN)

        weather = weather_for_origin()
        weather.loc[2, "ghi"] = np.nan
        with self.assertRaisesRegex(ElnetIntradayInputError, "NaN or infinite"):
            build_elnet_intraday_features(weather, ORIGIN)

    def test_2345_origin_returns_empty_result(self):
        origin = pd.Timestamp("2026-06-01 23:45", tz="Europe/Bucharest")
        result = predict_elnet_intraday(pd.DataFrame(), origin, 0.0, bundle=fake_bundle())
        self.assertTrue(result.empty)


class ElnetProductionTests(unittest.TestCase):
    def test_completed_quarter_uses_integrated_energy(self):
        energy = calculate_elnet_interval_energy(
            production_readings(values=(2.0, 4.0, 6.0, 8.0)),
            ORIGIN - pd.Timedelta(minutes=15),
            ORIGIN,
        )
        self.assertAlmostEqual(energy, 1.25)
        self.assertNotAlmostEqual(energy, 8.0 * 0.25)

    def test_origin_reads_elnet_and_returns_completed_quarter_energy(self):
        calls = []

        def getter(asset, *, start, end):
            calls.append((asset, start, end))
            return production_readings()

        origin, energy = get_latest_elnet_forecast_origin(
            now=ORIGIN, readings_getter=getter
        )
        self.assertEqual(origin, ORIGIN)
        self.assertEqual(energy, 0.8125)
        self.assertEqual(calls[0][0], "elnet")
        self.assertEqual(calls[0][1].tzinfo, timezone.utc)
        self.assertEqual(calls[0][2].tzinfo, timezone.utc)

    def test_missing_production_fails_clearly(self):
        with self.assertRaisesRegex(ElnetIntradayInputError, "No Elnet power samples"):
            get_latest_elnet_forecast_origin(
                now=ORIGIN, readings_getter=lambda *args, **kwargs: []
            )


class ElnetCorrectionTests(unittest.TestCase):
    def test_actual_residual_starts_full_and_has_sixty_minute_half_life(self):
        result = predict_elnet_intraday(
            weather_for_origin(), ORIGIN, 1.4, bundle=fake_bundle()
        )
        self.assertEqual(CORRECTION_HALF_LIFE_MINUTES, 60.0)
        self.assertEqual(result["Reference_DAM_Prediction"].iloc[0], 1.0)
        self.assertEqual(result["Actual_minus_DAM"].iloc[0], 0.4)
        self.assertEqual(result["Correction_weight"].iloc[0], 1.0)
        self.assertEqual(result["Prediction_ID"].iloc[0], 1.4)
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[4], 75)
        self.assertEqual(result["Correction_weight"].iloc[4], 0.5)
        self.assertEqual(result["Prediction_ID"].iloc[4], 1.2)

    def test_dark_targets_are_zero(self):
        weather = weather_for_origin()
        weather.loc[1, "ghi"] = 0
        result = predict_elnet_intraday(
            weather, ORIGIN, 1.4, bundle=fake_bundle()
        )
        self.assertEqual(result["Prediction_DAM"].iloc[0], 0)
        self.assertEqual(result["Prediction_ID"].iloc[0], 0)

    def test_orchestration_writes_distinct_elnet_output(self):
        with TemporaryDirectory() as directory:
            weather_path = Path(directory) / "weather.csv"
            result_path = Path(directory) / "elnet_intraday.xlsx"
            weather_for_origin().to_csv(weather_path, index=False)
            result = run_elnet_intraday_forecast(
                now=ORIGIN,
                readings_getter=lambda *args, **kwargs: production_readings(),
                weather_path=weather_path,
                result_path=result_path,
            )
            self.assertTrue(result_path.is_file())
            self.assertTrue((result["Last_Productie"] == 0.8125).all())
            self.assertNotIn("Last_Power_MW", result.columns)


if __name__ == "__main__":
    unittest.main()
