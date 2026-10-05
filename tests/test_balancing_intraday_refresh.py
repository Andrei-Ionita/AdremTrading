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
    RENEWABLE_ENERGY_HOLDING_INTRADAY_CONFIG,
    PortfolioIntradayInputError,
    ULMENI_INTRADAY_CONFIG,
)


class IntradayRefreshTests(unittest.TestCase):
    def test_motif_is_not_a_portfolio_dependency(self):
        import balancing

        self.assertFalse(any('motif' in name.lower() for name in vars(balancing)))

    def test_motif_correction_is_not_scheduled(self):
        with (
            patch('balancing.run_portfolio_intraday_forecast', return_value='portfolio') as run,
            patch('balancing.run_elnet_intraday_forecast', return_value='elnet'),
            patch('balancing.run_horeco_intraday_forecast', return_value='horeco'),
            patch('balancing.run_hng_intraday_forecast', return_value='hng'),
            patch('balancing.run_incuba_intraday_forecast', return_value='incuba'),
        ):
            available, results, errors = refresh_intraday_corrections()
        self.assertNotIn('motif', available)
        self.assertNotIn('motif', results)
        self.assertEqual(errors, {})
        self.assertEqual(sum(available.values()), 14)
        self.assertNotIn('motif', [call.args[0].asset_key for call in run.call_args_list])

    def test_renewable_workbook_uses_only_active_delivery_dates(self):
        from portfolio_export import RENEWABLE_ENERGY_HOLDING_RESULTS_PATH

        frame = pd.DataFrame({
            'Data': pd.to_datetime(['2026-09-30 23:45', '2026-10-01 00:00']),
            'Interval': [96, 1],
            'Prediction_Renewable_Energy_Holding': [0, 0.237],
            'Lookup': ['30.09.202696', '01.10.20261'],
        })
        written = {}

        def capture(data, path, **kwargs):
            written[str(path)] = data.copy()

        with (
            patch('portfolio_export.build_quarter_hourly_portfolio', return_value=frame),
            patch('pathlib.Path.mkdir'),
            patch.object(pd.DataFrame, 'to_excel', capture),
        ):
            result = create_excel_file_with_all_forecasts_15min()
        pd.testing.assert_frame_equal(result, frame)
        self.assertIn('./Forecast_15min.xlsx', written)
        renewable = written[str(RENEWABLE_ENERGY_HOLDING_RESULTS_PATH)]
        self.assertEqual(renewable.Data.tolist(), [pd.Timestamp('2026-10-01')])
        self.assertEqual(renewable.Prediction.tolist(), [0.237])
        self.assertEqual(renewable.columns.tolist(), ['Data', 'Interval', 'Prediction', 'Lookup'])

    def test_gcsp_is_aggregated_into_hourly_portfolio_export(self):
        quarters = pd.DataFrame({
            "Data": pd.date_range("2026-09-28 09:00", periods=8, freq="15min"),
            "Interval": range(37, 45),
            "Prediction_GCSP": [0.1, 0.2, 0.3, 0.4, 0.2, 0.2, 0.2, 0.2],
        })
        with patch("balancing.pd.DataFrame.to_excel"):
            result = create_excel_file_with_all_forecasts(quarters)
        self.assertEqual(result["Prediction_GCSP"].tolist(), [1.0, 0.8])
        self.assertEqual(result["Interval"].tolist(), [10, 11])
        self.assertLess(result.columns.get_loc("Prediction_GCSP"), result.columns.get_loc("Lookup"))

    def test_gcsp_is_included_in_15min_portfolio_export(self):
        timestamps = pd.to_datetime(["2026-09-10 10:15", "2026-09-10 10:30"])
        dam = pd.DataFrame(
            {
                "Data": timestamps,
                "Interval": [42, 43],
                "Prediction": [0.4, 0.3],
                "Lookup": ["unused", "unused"],
            }
        )
        gcsp = dam.assign(Prediction=[0.25, 0.2])

        def read_excel(path, *args, **kwargs):
            if str(path).endswith("Results_Production_GCSP_xgb_15min.xlsx"):
                return gcsp.copy()
            if str(path).endswith("Forecast_template.xlsx"):
                return pd.DataFrame(index=range(len(dam)))
            return dam.copy()

        with (
            patch("balancing.pd.read_excel", side_effect=read_excel),
            patch("balancing.pd.DataFrame.to_excel"),
            patch("pathlib.Path.is_file", return_value=False),
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
                use_ferma_intraday=False,
                use_necaluxan_intraday=False,
                use_ulmeni_intraday=False,
                use_start_fotovoltaice_intraday=False,
                use_anasun_intraday=False,
            )

        self.assertEqual(result["Prediction_GCSP"].tolist(), [0.25, 0.2])
        self.assertLess(
            result.columns.get_loc("Prediction_GCSP"),
            result.columns.get_loc("Lookup"),
        )

    def test_anasun_is_aggregated_into_hourly_portfolio_export(self):
        quarters = pd.DataFrame({
            "Data": pd.date_range("2026-09-28 09:00", periods=8, freq="15min"),
            "Interval": range(37, 45),
            "Prediction_AnaSun": [0.1, 0.2, 0.3, 0.4, 0.2, 0.2, 0.2, 0.2],
        })
        with patch("balancing.pd.DataFrame.to_excel"):
            result = create_excel_file_with_all_forecasts(quarters)
        self.assertEqual(result["Prediction_AnaSun"].tolist(), [1.0, 0.8])
        self.assertEqual(result["Interval"].tolist(), [10, 11])
        self.assertLess(result.columns.get_loc("Prediction_AnaSun"), result.columns.get_loc("Lookup"))

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

    def test_missing_renewable_production_disables_only_its_correction(self):
        def run(config):
            if config == RENEWABLE_ENERGY_HOLDING_INTRADAY_CONFIG:
                raise PortfolioIntradayInputError('No current Renewable Energy Holding samples')
            return 'fresh'

        with (
            patch('balancing.run_portfolio_intraday_forecast', side_effect=run),
            patch('balancing.run_elnet_intraday_forecast', return_value='elnet'),
            patch('balancing.run_horeco_intraday_forecast', return_value='horeco'),
            patch('balancing.run_hng_intraday_forecast', return_value='hng'),
            patch('balancing.run_incuba_intraday_forecast', return_value='incuba'),
        ):
            available, results, errors = refresh_intraday_corrections()
        self.assertFalse(available['renewable_energy_holding'])
        self.assertTrue(available['elnet'])
        self.assertNotIn('renewable_energy_holding', results)
        self.assertEqual(set(errors), {'Renewable Energy Holding'})

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
                "ferma",
                "necaluxan",
                "ulmeni",
                "start_fotovoltaice",
                "anasun",
                "renewable_energy_holding",
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
                ("elnet", "horeco", "incuba"),
                ("anto", "ferma", "start_fotovoltaice", "renewable_energy_holding"),
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
                use_ferma_intraday=False,
                use_necaluxan_intraday=False,
                use_ulmeni_intraday=False,
                use_start_fotovoltaice_intraday=False,
                use_anasun_intraday=True,
            )

        self.assertEqual(result["Prediction_AnaSun"].tolist(), [1.4, 0.9])


if __name__ == "__main__":
    unittest.main()
