from __future__ import annotations

import unittest
from datetime import timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from horeco_intraday import (
    CORRECTION_HALF_LIFE_MINUTES,
    HORECO_BASELINE_FEATURES,
    HORECO_DAM_MODEL_PATH,
    HORECO_MAX_INTERVAL_ENERGY_MWH,
    MIN_ACTUAL_TO_FORECAST_RATIO,
    HorecoBaselineModel,
    HorecoIntradayInputError,
    build_horeco_baseline_features,
    calculate_horeco_interval_energy,
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

    def __init__(self, value: float = 0.25):
        self.value = value

    def predict(self, features):
        return np.full(len(features), self.value)


def fake_model(value: float = 0.25) -> HorecoBaselineModel:
    return HorecoBaselineModel(model=ConstantModel(value))


def production_readings(
    values=(1.25, 1.25, 1.25, 1.25),
    timestamps=("09:45", "09:50", "09:55", "10:00"),
):
    return [
        PowerReading(
            "horeco",
            pd.Timestamp(f"2026-07-29 {timestamp}", tz="Europe/Bucharest")
            .tz_convert("UTC")
            .isoformat(),
            value,
            None,
            None,
            "test",
        )
        for timestamp, value in zip(timestamps, values)
    ]


class HorecoModelTests(unittest.TestCase):
    def test_existing_dam_model_loads_with_exact_feature_order(self):
        self.assertTrue(Path(HORECO_DAM_MODEL_PATH).is_file())
        loaded = load_horeco_baseline_model()
        self.assertEqual(loaded.feature_columns, HORECO_BASELINE_FEATURES)
        self.assertEqual(loaded.model.n_features_in_, 6)
        self.assertEqual(loaded.plant_max_output, HORECO_MAX_INTERVAL_ENERGY_MWH)

    def test_real_model_smoke_prediction(self):
        result = predict_horeco_intraday(weather_for_origin(), ORIGIN, 0.3)
        self.assertEqual(len(result), 55)
        self.assertTrue(np.isfinite(result["Prediction_ID"]).all())
        self.assertTrue((result["Prediction_ID"] >= 0).all())
        self.assertTrue((result["Prediction_ID"] <= HORECO_MAX_INTERVAL_ENERGY_MWH).all())


class HorecoFeatureTests(unittest.TestCase):
    def test_origin_generates_only_remaining_same_day_targets(self):
        targets, features = build_horeco_baseline_features(weather_for_origin(), ORIGIN)
        self.assertEqual(len(targets), 55)
        self.assertEqual(targets[0], pd.Timestamp("2026-07-29 10:15", tz="Europe/Bucharest"))
        self.assertEqual(targets[-1], pd.Timestamp("2026-07-29 23:45", tz="Europe/Bucharest"))
        self.assertTrue((targets.date == ORIGIN.date()).all())
        self.assertEqual(tuple(features.columns), HORECO_BASELINE_FEATURES)

        result = predict_horeco_intraday(
            weather_for_origin(), ORIGIN, 0.3, baseline_model=fake_model()
        )
        np.testing.assert_array_equal(
            result["Forecast_horizon_minutes"].to_numpy(),
            np.arange(15, 826, 15),
        )

    def test_weather_is_matched_to_target_timestamp(self):
        weather = weather_for_origin().sample(frac=1, random_state=7).reset_index(drop=True)
        _, features = build_horeco_baseline_features(weather, ORIGIN)
        expected = weather.loc[weather["period_end"] == "2026-07-29T07:15:00Z"].iloc[0]
        self.assertEqual(features["Temperatura"].iloc[0], expected["air_temp"])
        self.assertEqual(features["Radiatie"].iloc[0], expected["ghi"])

    def test_missing_or_invalid_weather_fails_clearly(self):
        with self.assertRaisesRegex(HorecoIntradayInputError, "missing 1 required intervals"):
            build_horeco_baseline_features(weather_for_origin().iloc[:-1], ORIGIN)

        weather = weather_for_origin()
        weather.loc[2, "ghi"] = np.nan
        with self.assertRaisesRegex(HorecoIntradayInputError, "NaN or infinite"):
            build_horeco_baseline_features(weather, ORIGIN)

    def test_2345_origin_returns_empty_result(self):
        origin = pd.Timestamp("2026-07-29 23:45", tz="Europe/Bucharest")
        result = predict_horeco_intraday(
            pd.DataFrame(), origin, 0.0, baseline_model=fake_model()
        )
        self.assertTrue(result.empty)


class HorecoProductionTests(unittest.TestCase):
    def test_completed_quarter_uses_integrated_energy(self):
        energy = calculate_horeco_interval_energy(
            production_readings(values=(0.4, 0.8, 1.2, 1.6)),
            ORIGIN - pd.Timedelta(minutes=15),
            ORIGIN,
        )
        self.assertAlmostEqual(energy, 0.25)
        self.assertNotAlmostEqual(energy, 1.6 * 0.25)

    def test_origin_reads_horeco_and_returns_completed_quarter_energy(self):
        calls = []

        def getter(asset, *, start, end):
            calls.append((asset, start, end))
            return production_readings()

        origin, energy = get_latest_horeco_forecast_origin(
            now=ORIGIN, readings_getter=getter
        )
        self.assertEqual(origin, ORIGIN)
        self.assertEqual(energy, 0.3125)
        self.assertEqual(calls[0][0], "horeco")
        self.assertEqual(calls[0][1].tzinfo, timezone.utc)
        self.assertEqual(calls[0][2].tzinfo, timezone.utc)

    def test_origin_rolls_back_one_quarter_to_latest_supported_boundary(self):
        latest = PowerReading(
            "horeco",
            pd.Timestamp("2026-07-29 10:07", tz="Europe/Bucharest")
            .tz_convert("UTC")
            .isoformat(),
            1.25,
            None,
            None,
            "test",
        )
        origin, energy = get_latest_horeco_forecast_origin(
            now=ORIGIN + pd.Timedelta(minutes=29),
            readings_getter=lambda *args, **kwargs: production_readings(),
            latest_reading_getter=lambda *args, **kwargs: latest,
        )
        self.assertEqual(origin, ORIGIN)
        self.assertEqual(energy, 0.3125)

    def test_missing_production_fails_clearly(self):
        with self.assertRaisesRegex(HorecoIntradayInputError, "No Horeco power samples"):
            get_latest_horeco_forecast_origin(
                now=ORIGIN, readings_getter=lambda *args, **kwargs: []
            )


class HorecoCorrectionTests(unittest.TestCase):
    def test_severe_downward_deviation_keeps_dam_forecast(self):
        result = predict_horeco_intraday(
            weather_for_origin(), ORIGIN, 0.124, baseline_model=fake_model()
        )
        self.assertEqual(MIN_ACTUAL_TO_FORECAST_RATIO, 0.5)
        self.assertTrue((result["Prediction_ID"] == result["Prediction_DAM"]).all())
        self.assertTrue((result["Correction_weight"] == 0).all())

    def test_actual_residual_starts_full_and_has_two_hour_half_life(self):
        result = predict_horeco_intraday(
            weather_for_origin(), ORIGIN, 0.4, baseline_model=fake_model()
        )
        self.assertEqual(CORRECTION_HALF_LIFE_MINUTES, 120.0)
        self.assertEqual(result["Reference_DAM_Prediction"].iloc[0], 0.25)
        self.assertEqual(result["Actual_minus_DAM"].iloc[0], 0.15)
        self.assertEqual(result["Correction_weight"].iloc[0], 1.0)
        self.assertEqual(result["Prediction_ID"].iloc[0], 0.4)
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[4], 75)
        self.assertEqual(result["Correction_weight"].iloc[4], 0.7071)
        self.assertEqual(result["Prediction_ID"].iloc[4], 0.356)
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[8], 135)
        self.assertEqual(result["Correction_weight"].iloc[8], 0.5)

    def test_dark_targets_are_zero(self):
        weather = weather_for_origin()
        weather.loc[1, "ghi"] = 0
        result = predict_horeco_intraday(
            weather, ORIGIN, 0.4, baseline_model=fake_model()
        )
        self.assertEqual(result["Prediction_DAM"].iloc[0], 0)
        self.assertEqual(result["Prediction_ID"].iloc[0], 0)

    def test_orchestration_writes_corrected_output_without_instantaneous_power(self):
        with TemporaryDirectory() as directory:
            weather_path = Path(directory) / "weather.csv"
            result_path = Path(directory) / "horeco_intraday.xlsx"
            model_path = Path(directory) / "model.joblib"
            weather_for_origin().to_csv(weather_path, index=False)
            import joblib

            joblib.dump(ConstantModel(), model_path)
            result = run_horeco_intraday_forecast(
                now=ORIGIN,
                readings_getter=lambda *args, **kwargs: production_readings(),
                model_path=model_path,
                weather_path=weather_path,
                result_path=result_path,
            )
            self.assertTrue(result_path.is_file())
            self.assertTrue((result["Last_Productie"] == 0.3125).all())
            self.assertNotIn("Last_Power_MW", result.columns)
            exported = pd.read_excel(result_path)
            self.assertEqual(list(exported.columns), list(result.columns))


if __name__ == "__main__":
    unittest.main()
