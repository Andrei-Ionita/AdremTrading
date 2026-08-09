import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from incuba_intraday import (
    ADREM_TO_INCUBA_SCALE,
    CORRECTION_HALF_LIFE_MINUTES,
    INCUBA_MAX_INTERVAL_ENERGY_MWH,
    MIN_ACTUAL_TO_FORECAST_RATIO,
    IncubaIntradayInputError,
    calculate_incuba_interval_energy,
    get_latest_incuba_forecast_origin,
    predict_incuba_intraday,
    run_incuba_intraday_forecast,
)
from power_reading.service import PowerReading


ORIGIN = pd.Timestamp("2026-06-01 10:00", tz="Europe/Bucharest")


def adrem_forecast_for_origin(origin=ORIGIN, prediction=0.14):
    targets = pd.date_range(
        origin + pd.Timedelta(minutes=15),
        origin.normalize() + pd.Timedelta(hours=23, minutes=45),
        freq="15min",
        tz="Europe/Bucharest",
    )
    return pd.DataFrame(
        {
            "Data": targets.tz_localize(None),
            "Interval": targets.hour * 4 + targets.minute // 15 + 1,
            "Prediction": prediction,
            "Lookup": "unused",
        }
    )


def weather_for_origin(origin=ORIGIN, ghi=100.0):
    targets = pd.date_range(
        origin + pd.Timedelta(minutes=15),
        origin.normalize() + pd.Timedelta(hours=23, minutes=45),
        freq="15min",
        tz="Europe/Bucharest",
    )
    return pd.DataFrame(
        {
            "period_end": targets.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ghi": ghi,
        }
    )


def production_readings(
    values=(0.4, 0.4, 0.4, 0.4),
    timestamps=("09:45", "09:50", "09:55", "10:00"),
):
    return [
        PowerReading(
            "incuba",
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


class IncubaPredictionTests(unittest.TestCase):
    def test_severe_downward_deviation_keeps_derived_dam_forecast(self):
        result = predict_incuba_intraday(
            adrem_forecast_for_origin(), weather_for_origin(), ORIGIN, 0.049
        )
        self.assertEqual(MIN_ACTUAL_TO_FORECAST_RATIO, 0.5)
        self.assertTrue((result["Prediction_ID"] == result["Prediction_DAM"]).all())
        self.assertTrue((result["Correction_weight"] == 0).all())

    def test_baseline_uses_exact_adrem_capacity_ratio(self):
        self.assertEqual(ADREM_TO_INCUBA_SCALE, 0.998 / 1.4)
        result = predict_incuba_intraday(
            adrem_forecast_for_origin(prediction=0.14),
            weather_for_origin(),
            ORIGIN,
            0.0998,
        )
        self.assertEqual(result["Prediction_DAM"].iloc[0], 0.1)
        self.assertEqual(result["Actual_minus_DAM"].iloc[0], 0.0)

    def test_origin_generates_only_remaining_delivery_day_targets(self):
        result = predict_incuba_intraday(
            adrem_forecast_for_origin(), weather_for_origin(), ORIGIN, 0.2
        )
        self.assertEqual(len(result), 55)
        self.assertEqual(result["Data"].iloc[0], pd.Timestamp("2026-06-01 10:15"))
        self.assertEqual(result["Data"].iloc[-1], pd.Timestamp("2026-06-01 23:45"))
        self.assertTrue((result["Data"].dt.date == ORIGIN.date()).all())
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[0], 15)
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[-1], 825)

    def test_actual_residual_is_strong_first_and_decays(self):
        result = predict_incuba_intraday(
            adrem_forecast_for_origin(), weather_for_origin(), ORIGIN, 0.2
        )
        self.assertEqual(CORRECTION_HALF_LIFE_MINUTES, 120.0)
        self.assertEqual(result["Prediction_ID"].iloc[0], 0.2)
        self.assertEqual(result["Correction_weight"].iloc[0], 1.0)
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[4], 75)
        self.assertEqual(result["Correction_weight"].iloc[4], 0.7071)
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[8], 135)
        self.assertEqual(result["Correction_weight"].iloc[8], 0.5)
        self.assertEqual(result["Last_Productie"].nunique(), 1)

    def test_delayed_origin_starts_at_next_future_target(self):
        result = predict_incuba_intraday(
            adrem_forecast_for_origin(),
            weather_for_origin(),
            ORIGIN,
            0.2,
            target_start=ORIGIN + pd.Timedelta(minutes=29),
        )
        self.assertEqual(result["Data"].iloc[0], pd.Timestamp("2026-06-01 10:30"))
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[0], 30)

    def test_dark_targets_are_zero_and_predictions_are_capped(self):
        weather = weather_for_origin()
        weather.loc[0, "ghi"] = 0
        result = predict_incuba_intraday(
            adrem_forecast_for_origin(prediction=1.0),
            weather,
            ORIGIN,
            1.0,
        )
        self.assertEqual(result["Prediction_ID"].iloc[0], 0)
        self.assertTrue(
            (result["Prediction_ID"] <= np.round(INCUBA_MAX_INTERVAL_ENERGY_MWH, 3)).all()
        )

    def test_missing_baseline_or_weather_interval_fails_clearly(self):
        adrem = adrem_forecast_for_origin().iloc[1:].copy()
        with self.assertRaisesRegex(IncubaIntradayInputError, "missing 1 required intervals"):
            predict_incuba_intraday(adrem, weather_for_origin(), ORIGIN, 0.2)

        weather = weather_for_origin().iloc[1:].copy()
        with self.assertRaisesRegex(IncubaIntradayInputError, "missing 1 required intervals"):
            predict_incuba_intraday(adrem_forecast_for_origin(), weather, ORIGIN, 0.2)

    def test_invalid_actual_energy_fails_clearly(self):
        with self.assertRaisesRegex(IncubaIntradayInputError, "Last_Productie must be finite"):
            predict_incuba_intraday(
                adrem_forecast_for_origin(), weather_for_origin(), ORIGIN, np.nan
            )


class IncubaProductionTests(unittest.TestCase):
    def test_completed_interval_uses_trapezoidal_energy_integration(self):
        energy = calculate_incuba_interval_energy(
            production_readings(values=(0.2, 0.4, 0.6, 0.8)),
            ORIGIN - pd.Timedelta(minutes=15),
            ORIGIN,
        )
        self.assertAlmostEqual(energy, 0.125)
        self.assertNotAlmostEqual(energy, 0.8 * 0.25)

    def test_origin_uses_incuba_readings(self):
        calls = []

        def getter(asset, *, start, end):
            calls.append((asset, start, end))
            return production_readings()

        origin, energy = get_latest_incuba_forecast_origin(
            now=ORIGIN,
            readings_getter=getter,
        )
        self.assertEqual(origin, ORIGIN)
        self.assertAlmostEqual(energy, 0.1)
        self.assertEqual(calls[0][0], "incuba")

    def test_origin_uses_latest_completed_quarter_at_inference_time(self):
        origin, energy = get_latest_incuba_forecast_origin(
            now=ORIGIN + pd.Timedelta(minutes=29),
            readings_getter=lambda *args, **kwargs: production_readings(
                timestamps=("10:00", "10:05", "10:10", "10:15")
            ),
            latest_reading_getter=lambda *args, **kwargs: self.fail(
                "latest reading must not select the completed interval"
            ),
        )
        self.assertEqual(origin, ORIGIN + pd.Timedelta(minutes=15))
        self.assertAlmostEqual(energy, 0.1)

    def test_run_path_writes_incuba_workbook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adrem_path = root / "adrem.xlsx"
            weather_path = root / "weather.csv"
            result_path = root / "incuba.xlsx"
            adrem_forecast_for_origin().to_excel(adrem_path, index=False)
            weather_for_origin().to_csv(weather_path, index=False)

            result = run_incuba_intraday_forecast(
                now=ORIGIN,
                readings_getter=lambda *args, **kwargs: production_readings(),
                adrem_forecast_path=adrem_path,
                weather_path=weather_path,
                result_path=result_path,
            )

            self.assertTrue(result_path.is_file())
            self.assertEqual(len(result), 55)
            self.assertEqual(pd.read_excel(result_path).columns.tolist(), result.columns.tolist())


if __name__ == "__main__":
    unittest.main()
