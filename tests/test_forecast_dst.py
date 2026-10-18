import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

import ml
import portfolio_intraday as intraday
from forecast_time import delivery_times, local_day_end, round_delivery, utc_strings
from incuba_intraday import predict_incuba_intraday
from portfolio_export import ASSET_FILES, CORRECTIONS, aggregate_hourly_portfolio, build_quarter_hourly_portfolio, write_portfolio_excel


def baseline(times, prediction=0.1):
    return pd.DataFrame({"Data": times.tz_localize(None), "Data_UTC": utc_strings(times),
                         "Interval": times.hour * 4 + times.minute // 15 + 1, "Prediction": prediction})


def weather(times):
    return pd.DataFrame({"period_end": utc_strings(times), "ghi": 100.0, "air_temp": 20.0,
                         "cloud_opacity": 10.0, "dewpoint_temp": 5.0, "relative_humidity": 50.0,
                         "azimuth": 0.0, "zenith": 0.0})


class ForecastDstTests(unittest.TestCase):
    def test_all_generators_keep_each_real_interval_and_use_local_clock(self):
        times = pd.date_range("2026-10-25", "2026-10-26", freq="15min",
                              tz="Europe/Bucharest", inclusive="left")
        model = Mock()
        model.n_features_in_ = 6
        model.predict.side_effect = lambda data: np.full(len(data), 0.1)
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp)
                for suffix, directory, filename, location in ASSET_FILES:
                    with self.subTest(asset=suffix):
                        path = Path(directory)
                        (path / "Solcast").mkdir(parents=True, exist_ok=True)
                        weather(times).to_csv(path / "Solcast" / f"{location}_15min.csv", index=False)
                        function = getattr(ml, f"predicting_exporting_{filename}_15min")
                        with patch("ml.joblib.load", return_value=model):
                            function(1, 24, 0)
                        output = pd.read_excel(path / f"Results_Production_{filename}_xgb_15min.xlsx")
                        self.assertEqual(len(output), 100)
                        self.assertEqual(output.Data.duplicated().sum(), 4)
                        pd.testing.assert_index_equal(delivery_times(output), times, check_names=False)
                        self.assertEqual(output.Interval.tolist(), (times.hour * 4 + times.minute // 15 + 1).tolist())
            finally:
                os.chdir(previous)

    def test_exports_preserve_23_24_25_hour_energy_and_unique_delivery_keys(self):
        for day, count in (("2026-03-29", 92), ("2026-10-18", 96), ("2026-10-25", 100)):
            with self.subTest(day=day):
                start = pd.Timestamp(day, tz="Europe/Bucharest")
                times = pd.date_range(start, start + pd.DateOffset(days=1), freq="15min", inclusive="left")
                frame = baseline(times)
                with patch("pandas.read_excel", return_value=frame):
                    result = build_quarter_hourly_portfolio({})
                self.assertEqual(len(result), count)
                self.assertFalse(result.Lookup.duplicated().any())
                self.assertFalse(result.Data_UTC.duplicated().any())
                hourly = aggregate_hourly_portfolio(result)
                self.assertEqual(len(hourly), count // 4)
                self.assertEqual(hourly.Interval.tolist(), list(range(1, count // 4 + 1)))
                self.assertFalse(hourly.Lookup.duplicated().any())
                for column in result.filter(like="Prediction_"):
                    self.assertAlmostEqual(result[column].sum(), hourly[column].sum())
                for output in (result, hourly):
                    buffer = io.BytesIO()
                    write_portfolio_excel(output, buffer)
                    buffer.seek(0)
                    reread = pd.read_excel(buffer)
                    self.assertNotIn('Data_UTC', reread.columns)
                    self.assertEqual(len(reread), len(output))
                    np.testing.assert_allclose(reread.filter(like='Prediction_'), output.filter(like='Prediction_'))
                    self.assertTrue(reread.Data.eq(reread.Data.dt.normalize()).all())
                    self.assertEqual(reread.Interval.tolist(), output.Interval.tolist())
                    self.assertFalse(reread.Lookup.duplicated().any())

    def test_every_correction_survives_future_dst_rows(self):
        origin = pd.Timestamp("2026-10-18 12:00", tz="Europe/Bucharest")
        times = pd.date_range(origin, pd.Timestamp("2026-10-25 12:00", tz="Europe/Bucharest"), freq="15min")
        frame = baseline(times).drop(columns="Data_UTC")
        for config in vars(intraday).values():
            if not isinstance(config, intraday.PortfolioIntradayConfig) or config.asset_key == "motif":
                continue
            with self.subTest(asset=config.asset_key):
                result = intraday.predict_portfolio_intraday(config, frame, weather(times), origin, 0.08)
                self.assertEqual(result.Data.iloc[-1], pd.Timestamp("2026-10-18 23:45"))
                self.assertGreater(result.Correction_weight.iloc[0], 0)
                self.assertAlmostEqual(result.Prediction_ID.iloc[0], 0.08, places=3)
        result = predict_incuba_intraday(frame, weather(times), origin, 0.08)
        self.assertAlmostEqual(result.Prediction_ID.iloc[0], 0.08, places=3)

    def test_corrections_work_inside_both_folds_and_end_at_local_midnight(self):
        for value in ("2026-10-25 00:15Z", "2026-10-25 01:15Z", "2026-03-29 00:15Z"):
            origin = pd.Timestamp(value).tz_convert("Europe/Bucharest")
            times = pd.date_range(origin.normalize(), local_day_end(origin), freq="15min")
            frame = baseline(times)
            self.assertEqual(round_delivery(origin + pd.Timedelta(minutes=2), "floor"), origin)
            config = intraday.NECALUXAN_INTRADAY_CONFIG
            for result in (intraday.predict_portfolio_intraday(config, frame, weather(times), origin, 0.08),
                           predict_incuba_intraday(frame, weather(times), origin, 0.08)):
                self.assertEqual(result.Data.iloc[-1].strftime("%H:%M"), "23:45")
                self.assertTrue(result.Data.dt.date.eq(origin.date()).all())
                self.assertFalse(result.Data_UTC.duplicated().any())
                self.assertAlmostEqual(result.Prediction_ID.iloc[0], 0.08, places=3)

    def test_explicit_identity_handles_shuffled_folds_and_rejects_mismatches(self):
        times = pd.date_range("2026-10-25 00:00Z", periods=8, freq="15min").tz_convert("Europe/Bucharest")
        frame = baseline(times).sample(frac=1, random_state=4)
        self.assertEqual(len(delivery_times(frame).unique()), 8)
        frame.loc[frame.index[0], "Data"] += pd.Timedelta(hours=1)
        with self.assertRaisesRegex(ValueError, "disagree"):
            delivery_times(frame)

    def test_single_ambiguous_or_nonexistent_local_time_is_not_guessed(self):
        for stamp in ("2026-10-25 03:15", "2026-03-29 03:15"):
            with self.assertRaisesRegex(ValueError, "ambiguous or nonexistent"):
                delivery_times(pd.DataFrame({"Data": [stamp]}))

    def test_correction_overlay_distinguishes_both_repeated_hours(self):
        times = pd.date_range("2026-10-25 00:00Z", periods=8, freq="15min").tz_convert("Europe/Bucharest")
        corrected = baseline(times).rename(columns={"Prediction": "Prediction_ID"})
        corrected["Prediction_ID"] = np.arange(8) / 10
        paths = {str(path): corrected for _, path in CORRECTIONS.values()}
        with (patch("pandas.read_excel", side_effect=lambda path: paths.get(str(path), baseline(times))),
              patch("pathlib.Path.is_file", return_value=True)):
            result = build_quarter_hourly_portfolio(dict.fromkeys(CORRECTIONS, True))
        for suffix, _ in CORRECTIONS.values():
            np.testing.assert_allclose(result[f"Prediction_{suffix}"], np.arange(8) / 10)
        hourly = aggregate_hourly_portfolio(result)
        np.testing.assert_allclose(hourly.Prediction_AnaSun, [0.6, 2.2])


if __name__ == "__main__":
    unittest.main()
