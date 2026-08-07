import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from portfolio_intraday import (
    ANTO_INTRADAY_CONFIG,
    ASTRO_INTRADAY_CONFIG,
    CORRECTION_HALF_LIFE_MINUTES,
    FERMA_INTRADAY_CONFIG,
    IMPERIAL_INTRADAY_CONFIG,
    MOTIF_INTRADAY_CONFIG,
    PortfolioIntradayConfig,
    PortfolioIntradayInputError,
    calculate_interval_energy,
    get_latest_forecast_origin,
    predict_portfolio_intraday,
    run_portfolio_intraday_forecast,
)
from power_reading.service import PowerReading


ORIGIN = pd.Timestamp("2026-06-01 10:00", tz="Europe/Bucharest")
CONFIG = PortfolioIntradayConfig(
    asset_key="test_asset",
    display_name="Test Asset",
    dam_results_path=Path("dam.xlsx"),
    weather_path=Path("weather.csv"),
    intraday_results_path=Path("intraday.xlsx"),
)


def dam_forecast(origin=ORIGIN, prediction=1.0):
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


def production_readings(asset="test_asset", values=(2.0, 4.0, 6.0, 8.0)):
    timestamps = ("09:45", "09:50", "09:55", "10:00")
    return [
        PowerReading(
            asset,
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


class PortfolioConfigurationTests(unittest.TestCase):
    def test_only_approved_remaining_portfolio_assets_are_configured(self):
        configs = (
            ASTRO_INTRADAY_CONFIG,
            IMPERIAL_INTRADAY_CONFIG,
            ANTO_INTRADAY_CONFIG,
            MOTIF_INTRADAY_CONFIG,
            FERMA_INTRADAY_CONFIG,
        )
        self.assertEqual(
            {config.asset_key for config in configs},
            {"astro", "imperial", "anto", "motif", "ferma_frumusica"},
        )
        self.assertNotIn("snk", {config.asset_key for config in configs})
        self.assertNotIn("pcsun", {config.asset_key for config in configs})
        for config in configs:
            self.assertTrue(config.dam_results_path.is_file())
            self.assertTrue(config.weather_path.is_file())


class PortfolioPredictionTests(unittest.TestCase):
    def test_ten_o_clock_origin_generates_55_same_day_targets(self):
        result = predict_portfolio_intraday(
            CONFIG,
            dam_forecast(),
            weather_for_origin(),
            ORIGIN,
            1.4,
        )
        self.assertEqual(len(result), 55)
        self.assertEqual(result["Data"].iloc[0], pd.Timestamp("2026-06-01 10:15"))
        self.assertEqual(result["Data"].iloc[-1], pd.Timestamp("2026-06-01 23:45"))
        self.assertTrue((result["Data"].dt.date == ORIGIN.date()).all())

    def test_actual_residual_starts_full_and_decays(self):
        result = predict_portfolio_intraday(
            CONFIG,
            dam_forecast(),
            weather_for_origin(),
            ORIGIN,
            1.4,
        )
        self.assertEqual(CORRECTION_HALF_LIFE_MINUTES, 120.0)
        self.assertEqual(result["Prediction_ID"].iloc[0], 1.4)
        self.assertEqual(result["Correction_weight"].iloc[0], 1.0)
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[4], 75)
        self.assertEqual(result["Correction_weight"].iloc[4], 0.7071)
        self.assertEqual(result["Prediction_ID"].iloc[4], 1.283)
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[8], 135)
        self.assertEqual(result["Correction_weight"].iloc[8], 0.5)
        self.assertEqual(result["Last_Productie"].nunique(), 1)

    def test_delayed_origin_skips_elapsed_targets(self):
        result = predict_portfolio_intraday(
            CONFIG,
            dam_forecast(),
            weather_for_origin(),
            ORIGIN,
            1.4,
            target_start=ORIGIN + pd.Timedelta(minutes=29),
        )
        self.assertEqual(result["Data"].iloc[0], pd.Timestamp("2026-06-01 10:30"))
        self.assertEqual(result["Forecast_horizon_minutes"].iloc[0], 30)

    def test_dark_targets_are_zero_and_optional_cap_is_enforced(self):
        weather = weather_for_origin()
        weather.loc[0, "ghi"] = 0
        capped = replace(CONFIG, max_interval_energy_mwh=1.1)
        result = predict_portfolio_intraday(
            capped,
            dam_forecast(),
            weather,
            ORIGIN,
            2.0,
        )
        self.assertEqual(result["Prediction_ID"].iloc[0], 0)
        self.assertTrue((result["Prediction_ID"] <= 1.1).all())

    def test_missing_baseline_or_weather_fails_clearly(self):
        with self.assertRaisesRegex(PortfolioIntradayInputError, "missing 1 required"):
            predict_portfolio_intraday(
                CONFIG,
                dam_forecast().iloc[1:],
                weather_for_origin(),
                ORIGIN,
                1.0,
            )
        with self.assertRaisesRegex(PortfolioIntradayInputError, "missing 1 required"):
            predict_portfolio_intraday(
                CONFIG,
                dam_forecast(),
                weather_for_origin().iloc[1:],
                ORIGIN,
                1.0,
            )

    def test_invalid_actual_energy_fails_clearly(self):
        with self.assertRaisesRegex(PortfolioIntradayInputError, "must be finite"):
            predict_portfolio_intraday(
                CONFIG,
                dam_forecast(),
                weather_for_origin(),
                ORIGIN,
                np.nan,
            )


class PortfolioProductionTests(unittest.TestCase):
    def test_completed_quarter_uses_integrated_energy(self):
        energy = calculate_interval_energy(
            CONFIG,
            production_readings(),
            ORIGIN - pd.Timedelta(minutes=15),
            ORIGIN,
        )
        self.assertAlmostEqual(energy, 1.25)
        self.assertNotAlmostEqual(energy, 8.0 * 0.25)

    def test_origin_queries_only_configured_asset(self):
        calls = []

        def getter(asset, *, start, end):
            calls.append((asset, start, end))
            return production_readings(asset=asset)

        origin, energy = get_latest_forecast_origin(
            CONFIG,
            now=ORIGIN,
            readings_getter=getter,
        )
        self.assertEqual(origin, ORIGIN)
        self.assertAlmostEqual(energy, 1.25)
        self.assertEqual(calls[0][0], "test_asset")

    def test_origin_rolls_back_one_quarter_for_delayed_reader(self):
        latest = PowerReading(
            "test_asset",
            pd.Timestamp("2026-06-01 10:07", tz="Europe/Bucharest")
            .tz_convert("UTC")
            .isoformat(),
            8.0,
            None,
            None,
            "test",
        )
        origin, _ = get_latest_forecast_origin(
            CONFIG,
            now=ORIGIN + pd.Timedelta(minutes=29),
            readings_getter=lambda *args, **kwargs: production_readings(),
            latest_reading_getter=lambda *args, **kwargs: latest,
        )
        self.assertEqual(origin, ORIGIN)

    def test_run_writes_distinct_corrected_workbook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = replace(
                CONFIG,
                dam_results_path=root / "dam.xlsx",
                weather_path=root / "weather.csv",
                intraday_results_path=root / "corrected.xlsx",
            )
            dam_forecast().to_excel(config.dam_results_path, index=False)
            weather_for_origin().to_csv(config.weather_path, index=False)

            result = run_portfolio_intraday_forecast(
                config,
                now=ORIGIN,
                readings_getter=lambda *args, **kwargs: production_readings(),
            )

            self.assertTrue(config.intraday_results_path.is_file())
            self.assertEqual(len(result), 55)
            self.assertEqual(
                pd.read_excel(config.intraday_results_path).columns.tolist(),
                result.columns.tolist(),
            )


if __name__ == "__main__":
    unittest.main()
