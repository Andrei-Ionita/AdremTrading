from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from balancing import (
    create_excel_file_with_all_forecasts,
    create_excel_file_with_all_forecasts_15min,
    refresh_intraday_corrections,
)
from portfolio_intraday import (
    ANASUN_INTRADAY_CONFIG,
    MM_MV_INTRADAY_CONFIG,
    START_FOTOVOLTAICE_INTRADAY_CONFIG,
    START_FOTOVOLTAICE_SCALE,
    ULMENI_INTRADAY_CONFIG,
)


class IntradayRefreshTests(unittest.TestCase):
    def test_anasun_is_aggregated_into_hourly_portfolio_export(self):
        hourly = pd.DataFrame(
            {
                "Data": pd.to_datetime(["2026-08-19", "2026-08-19"]),
                "Interval": [10, 11],
                "Prediction": [2.0, 1.5],
                "Lookup": ["19.08.202610", "19.08.202611"],
            }
        )
        quarter_hourly = pd.DataFrame(
            {
                "Data": pd.date_range("2026-08-19 09:00", periods=8, freq="15min"),
                "Interval": range(37, 45),
                "Prediction": [0.1, 0.2, 0.3, 0.4, 0.2, 0.2, 0.2, 0.2],
            }
        )

        def read_excel(path, *args, **kwargs):
            if str(path).endswith("Results_Production_AnaSun_xgb_15min.xlsx"):
                return quarter_hourly.copy()
            if str(path).endswith("Forecast_template.xlsx"):
                return pd.DataFrame(index=range(len(hourly)))
            return hourly.copy()

        with (
            patch("balancing.pd.read_excel", side_effect=read_excel),
            patch("balancing.pd.DataFrame.to_excel"),
        ):
            result = create_excel_file_with_all_forecasts()

        self.assertEqual(result["Prediction_AnaSun"].tolist(), [1.0, 0.8])
        self.assertLess(
            result.columns.get_loc("Prediction_AnaSun"),
            result.columns.get_loc("Lookup"),
        )

    def test_failed_refresh_is_disabled_for_the_export(self):
        def fail():
            raise ValueError("missing fresh reading")

        refreshers = (
            ("working", "Working", lambda: "fresh", ValueError),
            ("failed", "Failed", fail, ValueError),
        )

        available, results, errors = refresh_intraday_corrections(refreshers)

        self.assertEqual(available, {"working": True, "failed": False})
        self.assertEqual(results, {"working": "fresh"})
        self.assertEqual(errors, {"Failed": "missing fresh reading"})

    def test_default_refresh_includes_astro(self):
        with (
            patch("balancing.run_portfolio_intraday_forecast", return_value="portfolio"),
            patch("balancing.run_elnet_intraday_forecast", return_value="elnet"),
            patch("balancing.run_horeco_intraday_forecast", return_value="horeco"),
            patch("balancing.run_hng_intraday_forecast", return_value="hng"),
            patch("balancing.run_incuba_intraday_forecast", return_value="incuba"),
        ):
            available, _, errors = refresh_intraday_corrections()

        self.assertEqual(
            set(available),
            {
                "astro",
                "imperial",
                "mm_mv",
                "elnet",
                "horeco",
                "hng",
                "incuba",
                "anto",
                "motif",
                "ferma",
                "necaluxan",
                "ulmeni",
                "start_fotovoltaice",
                "anasun",
            },
        )
        self.assertEqual(errors, {})

    def test_default_refresh_uses_bounded_portal_groups(self):
        submitted_groups = []
        configured_workers = []

        class ImmediateFuture:
            def __init__(self, value):
                self.value = value

            def result(self):
                return self.value

        class RecordingExecutor:
            def __init__(self, max_workers):
                configured_workers.append(max_workers)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def submit(self, runner, group):
                submitted_groups.append(tuple(item[0] for item in group))
                return ImmediateFuture(runner(group))

        with (
            patch("balancing.ThreadPoolExecutor", RecordingExecutor),
            patch("balancing.run_portfolio_intraday_forecast", return_value="portfolio"),
            patch("balancing.run_elnet_intraday_forecast", return_value="elnet"),
            patch("balancing.run_horeco_intraday_forecast", return_value="horeco"),
            patch("balancing.run_hng_intraday_forecast", return_value="hng"),
            patch("balancing.run_incuba_intraday_forecast", return_value="incuba"),
        ):
            available, _, errors = refresh_intraday_corrections()

        self.assertTrue(all(available.values()))
        self.assertEqual(errors, {})
        self.assertEqual(configured_workers, [3])
        self.assertEqual(
            submitted_groups,
            [
                ("astro", "imperial"),
                ("elnet", "horeco", "incuba", "motif"),
                ("anto", "ferma", "start_fotovoltaice"),
                ("mm_mv", "anasun"),
                ("hng",),
                ("necaluxan",),
                ("ulmeni",),
            ],
        )

    def test_ulmeni_correction_is_applied_to_portfolio_export(self):
        timestamps = pd.to_datetime(["2026-08-13 10:15", "2026-08-13 10:30"])
        dam = pd.DataFrame(
            {
                "Data": timestamps,
                "Interval": [42, 43],
                "Prediction": [0.4, 0.3],
                "Lookup": ["unused", "unused"],
            }
        )
        corrected = pd.DataFrame(
            {
                "Data": [timestamps[0]],
                "Prediction_ID": [0.7],
            }
        )

        def read_excel(path, *args, **kwargs):
            if str(path) == str(ULMENI_INTRADAY_CONFIG.intraday_results_path):
                return corrected.copy()
            if str(path).endswith("Forecast_template.xlsx"):
                return pd.DataFrame(index=range(len(dam)))
            return dam.copy()

        with (
            patch("balancing.pd.read_excel", side_effect=read_excel),
            patch("balancing.pd.DataFrame.to_excel"),
            patch("pathlib.Path.is_file", return_value=True),
        ):
            result = create_excel_file_with_all_forecasts_15min(
                use_astro_intraday=False,
                use_imperial_intraday=False,
                use_mm_mv_intraday=False,
                use_elnet_intraday=False,
                use_horeco_intraday=False,
                use_hng_intraday=False,
                use_incuba_intraday=False,
                use_anto_intraday=False,
                use_motif_intraday=False,
                use_ferma_intraday=False,
                use_necaluxan_intraday=False,
                use_ulmeni_intraday=True,
                use_start_fotovoltaice_intraday=False,
                use_anasun_intraday=False,
            )

        self.assertEqual(result["Prediction_SolEn_Ulmeni"].tolist(), [0.7, 0.3])

    def test_mm_mv_correction_is_applied_to_portfolio_export(self):
        timestamps = pd.to_datetime(["2026-08-13 10:15", "2026-08-13 10:30"])
        dam = pd.DataFrame(
            {
                "Data": timestamps,
                "Interval": [42, 43],
                "Prediction": [0.4, 0.3],
                "Lookup": ["unused", "unused"],
            }
        )
        corrected = pd.DataFrame(
            {"Data": [timestamps[0]], "Prediction_ID": [0.7]}
        )

        def read_excel(path, *args, **kwargs):
            if str(path) == str(MM_MV_INTRADAY_CONFIG.intraday_results_path):
                return corrected.copy()
            if str(path).endswith("Forecast_template.xlsx"):
                return pd.DataFrame(index=range(len(dam)))
            return dam.copy()

        with (
            patch("balancing.pd.read_excel", side_effect=read_excel),
            patch("balancing.pd.DataFrame.to_excel"),
            patch("pathlib.Path.is_file", return_value=True),
        ):
            result = create_excel_file_with_all_forecasts_15min(
                use_astro_intraday=False,
                use_imperial_intraday=False,
                use_mm_mv_intraday=True,
                use_elnet_intraday=False,
                use_horeco_intraday=False,
                use_hng_intraday=False,
                use_incuba_intraday=False,
                use_anto_intraday=False,
                use_motif_intraday=False,
                use_ferma_intraday=False,
                use_necaluxan_intraday=False,
                use_ulmeni_intraday=False,
                use_start_fotovoltaice_intraday=False,
                use_anasun_intraday=False,
            )

        self.assertEqual(result["Prediction_MM_MV"].tolist(), [0.7, 0.3])

    def test_start_fotovoltaice_uses_scaled_ulmeni_and_intraday_overlay(self):
        timestamps = pd.to_datetime(["2026-08-13 10:15", "2026-08-13 10:30"])
        dam = pd.DataFrame(
            {
                "Data": timestamps,
                "Interval": [42, 43],
                "Prediction": [0.444, 0.222],
                "Lookup": ["unused", "unused"],
            }
        )
        corrected = pd.DataFrame(
            {"Data": [timestamps[0]], "Prediction_ID": [0.12]}
        )

        def read_excel(path, *args, **kwargs):
            if str(path) == str(
                START_FOTOVOLTAICE_INTRADAY_CONFIG.intraday_results_path
            ):
                return corrected.copy()
            if str(path).endswith("Forecast_template.xlsx"):
                return pd.DataFrame(index=range(len(dam)))
            return dam.copy()

        with (
            patch("balancing.pd.read_excel", side_effect=read_excel),
            patch("balancing.pd.DataFrame.to_excel"),
            patch("pathlib.Path.is_file", return_value=True),
        ):
            result = create_excel_file_with_all_forecasts_15min(
                use_astro_intraday=False,
                use_imperial_intraday=False,
                use_mm_mv_intraday=False,
                use_elnet_intraday=False,
                use_horeco_intraday=False,
                use_hng_intraday=False,
                use_incuba_intraday=False,
                use_anto_intraday=False,
                use_motif_intraday=False,
                use_ferma_intraday=False,
                use_necaluxan_intraday=False,
                use_ulmeni_intraday=False,
                use_start_fotovoltaice_intraday=True,
                use_anasun_intraday=False,
            )

        self.assertEqual(
            result["Prediction_Start_Fotovoltaice"].round(6).tolist(),
            [0.12, round(0.222 * START_FOTOVOLTAICE_SCALE, 6)],
        )
        self.assertLess(
            result.columns.get_loc("Prediction_Start_Fotovoltaice"),
            result.columns.get_loc("Lookup"),
        )
        self.assertEqual(result["Prediction_AnaSun"].tolist(), dam["Prediction"].tolist())
        self.assertLess(
            result.columns.get_loc("Prediction_AnaSun"),
            result.columns.get_loc("Lookup"),
        )

    def test_anasun_correction_is_applied_to_portfolio_export(self):
        timestamps = pd.to_datetime(["2026-08-19 10:15", "2026-08-19 10:30"])
        dam = pd.DataFrame(
            {
                "Data": timestamps,
                "Interval": [42, 43],
                "Prediction": [1.0, 0.9],
                "Lookup": ["unused", "unused"],
            }
        )
        corrected = pd.DataFrame(
            {"Data": [timestamps[0]], "Prediction_ID": [1.4]}
        )

        def read_excel(path, *args, **kwargs):
            if str(path) == str(ANASUN_INTRADAY_CONFIG.intraday_results_path):
                return corrected.copy()
            if str(path).endswith("Forecast_template.xlsx"):
                return pd.DataFrame(index=range(len(dam)))
            return dam.copy()

        with (
            patch("balancing.pd.read_excel", side_effect=read_excel),
            patch("balancing.pd.DataFrame.to_excel"),
            patch("pathlib.Path.is_file", return_value=True),
        ):
            result = create_excel_file_with_all_forecasts_15min(
                use_astro_intraday=False,
                use_imperial_intraday=False,
                use_mm_mv_intraday=False,
                use_elnet_intraday=False,
                use_horeco_intraday=False,
                use_hng_intraday=False,
                use_incuba_intraday=False,
                use_anto_intraday=False,
                use_motif_intraday=False,
                use_ferma_intraday=False,
                use_necaluxan_intraday=False,
                use_ulmeni_intraday=False,
                use_start_fotovoltaice_intraday=False,
                use_anasun_intraday=True,
            )

        self.assertEqual(result["Prediction_AnaSun"].tolist(), [1.4, 0.9])


if __name__ == "__main__":
    unittest.main()
