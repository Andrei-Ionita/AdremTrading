from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd

from hng_intraday import get_latest_hng_forecast_origin
from portfolio_intraday import (
    ANTO_INTRADAY_CONFIG,
    get_latest_forecast_origin,
    predict_portfolio_intraday,
)
from power_reading.scrapers.adc_monitoring_scraper import (
    _adc_interval_energy_mwh,
    _adc_live_interval_estimate_mwh,
)
from power_reading.scrapers.fusionsolar_scraper import _fusionsolar_interval_energy_mwh
from power_reading.scrapers.imperial_scraper import _integrate_aurora_interval
from power_reading.scrapers.isolarcloud_scraper import (
    _integrate_isolar_interval,
    _parse_isolar_chart_tooltip,
)
from power_reading.scrapers.necaluxan_scraper import _vcom_interval_energy_mwh
from power_reading.scrapers.veltol_scraper import _veltol_interval_energy_mwh
from power_reading.service import (
    clear_interval_cache,
    read_interval_energy,
    read_latest_interval_energy,
)


START = datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=15)


class PortalIntervalParserTests(unittest.TestCase):
    def test_adc_estimate_blends_15m_average_and_live_power_equally(self):
        site = {"pvPowerAvg15MinKw": 1000.0, "pvPowerKw": 1400.0}
        self.assertAlmostEqual(
            _adc_live_interval_estimate_mwh(site, START, END),
            0.3,
        )

    def test_adc_estimate_requires_both_valid_inputs(self):
        with self.assertRaisesRegex(RuntimeError, "requires both"):
            _adc_live_interval_estimate_mwh(
                {"pvPowerKw": 1400.0}, START, END
            )

    def test_adc_blend_drives_a_decaying_portfolio_correction(self):
        origin = pd.Timestamp("2026-08-30 10:00", tz="Europe/Bucharest")
        targets = pd.date_range(
            origin + pd.Timedelta(minutes=15),
            origin.normalize() + pd.Timedelta(hours=23, minutes=45),
            freq="15min",
            tz="Europe/Bucharest",
        )
        dam = pd.DataFrame(
            {
                "Data": targets.tz_localize(None),
                "Interval": targets.hour * 4 + targets.minute // 15 + 1,
                "Prediction": 0.2,
            }
        )
        weather = pd.DataFrame(
            {
                "period_end": targets.tz_convert("UTC"),
                "ghi": 100.0,
            }
        )
        actual_energy = _adc_live_interval_estimate_mwh(
            {"pvPowerAvg15MinKw": 1000.0, "pvPowerKw": 1400.0},
            START,
            END,
        )

        result = predict_portfolio_intraday(
            ANTO_INTRADAY_CONFIG,
            dam,
            weather,
            origin,
            actual_energy,
        )

        self.assertAlmostEqual(actual_energy, 0.3)
        self.assertEqual(result["Prediction_ID"].iloc[0], 0.3)
        self.assertEqual(result["Correction"].iloc[0], 0.1)
        self.assertEqual(result["Prediction_ID"].iloc[8], 0.25)
        self.assertTrue((result["Prediction_ID"] >= 0).all())
        with self.assertRaisesRegex(RuntimeError, "invalid"):
            _adc_live_interval_estimate_mwh(
                {"pvPowerAvg15MinKw": float("nan"), "pvPowerKw": 1400.0},
                START,
                END,
            )

    def test_adc_integrates_covered_minute_averages(self):
        payload = {
            "data": [
                {
                    "ts": (START + timedelta(minutes=index)).isoformat(),
                    "pvPowerKw": 1200.0,
                    "coverageSeconds": 60,
                }
                for index in range(15)
            ]
        }
        self.assertAlmostEqual(_adc_interval_energy_mwh(payload, START, END), 0.3)

    def test_adc_rejects_incomplete_interval(self):
        payload = {
            "data": [
                {
                    "ts": (START + timedelta(minutes=index)).isoformat(),
                    "pvPowerKw": 1200.0,
                    "coverageSeconds": 60,
                }
                for index in range(10)
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "covers only"):
            _adc_interval_energy_mwh(payload, START, END)

    def test_veltol_uses_exact_quarter_average(self):
        local_start = START.astimezone(ZoneInfo("Europe/Bucharest"))
        payload = {
            "data": {
                "measurements": [
                    {"timeIso": local_start.isoformat(), "production": 2_000_000, "unit": "W"}
                ]
            }
        }
        self.assertAlmostEqual(_veltol_interval_energy_mwh(payload, local_start), 0.5)

    def test_fusionsolar_integrates_four_five_minute_boundaries(self):
        values = ["--"] * 288
        local_start = START.astimezone(ZoneInfo("Europe/Bucharest"))
        first = local_start.hour * 12 + local_start.minute // 5
        values[first:first + 4] = [1000, 2000, 3000, 4000]
        self.assertAlmostEqual(
            _fusionsolar_interval_energy_mwh({"data": {"productPower": values}}, START, END),
            0.625,
        )

    def test_fusionsolar_rejects_missing_boundary(self):
        values = [0] * 288
        local_start = START.astimezone(ZoneInfo("Europe/Bucharest"))
        first = local_start.hour * 12 + local_start.minute // 5
        values[first:first + 4] = [1000, "--", 3000, 4000]
        with self.assertRaisesRegex(ValueError, "missing power"):
            _fusionsolar_interval_energy_mwh(
                {"data": {"productPower": values}}, START, END
            )

    def test_aurora_integrates_chart_boundaries(self):
        points = [(START, 1.0), (START + timedelta(minutes=5), 2.0), (END, 4.0)]
        self.assertAlmostEqual(
            _integrate_aurora_interval(points, START, END, "test plant"),
            0.625,
        )

    def test_aurora_requires_both_interval_boundaries(self):
        with self.assertRaisesRegex(RuntimeError, "missing a boundary"):
            _integrate_aurora_interval([(START, 1.0)], START, END, "test plant")

    def test_isolarcloud_parses_and_integrates_chart_tooltips(self):
        parsed = _parse_isolar_chart_tooltip("08:30\nPV：3,999.2 kW")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[:2], (8, 30))
        self.assertAlmostEqual(parsed[2], 3.9992)
        points = [(START, 1.0), (START + timedelta(minutes=5), 2.0), (END, 4.0)]
        self.assertAlmostEqual(
            _integrate_isolar_interval(points, START, END, "test plant"),
            0.625,
        )

    def test_vcom_integrates_local_wall_time_power_series(self):
        local_start = START.astimezone(ZoneInfo("Europe/Bucharest"))
        values = [1000, 2000, 3000, 4000]
        data = []
        for index, value in enumerate(values):
            wall_time = (local_start + timedelta(minutes=5 * index)).replace(tzinfo=None)
            encoded_ms = int(wall_time.replace(tzinfo=timezone.utc).timestamp() * 1000)
            data.append([encoded_ms, value])
        payload = {"data": [{"name": "Power", "unit": "kW", "data": data}]}
        self.assertAlmostEqual(_vcom_interval_energy_mwh(payload, START, END), 0.625)


class OnDemandRoutingTests(unittest.TestCase):
    def tearDown(self):
        clear_interval_cache()

    @patch("power_reading.service._build_scraper")
    def test_service_reads_once_and_caches_same_interval(self, build_scraper):
        scraper = Mock()
        scraper.read_interval_energy.return_value = 0.42
        build_scraper.return_value = scraper

        self.assertEqual(read_interval_energy("hng", start=START, end=END), 0.42)
        self.assertEqual(read_interval_energy("hng", start=START, end=END), 0.42)
        scraper.read_interval_energy.assert_called_once_with(start=START, end=END)

    @patch(
        "power_reading.service.read_interval_energy",
        side_effect=(
            RuntimeError("Aurora PV Jucu chart is missing a boundary for the completed interval."),
            0.42,
        ),
    )
    def test_service_uses_latest_source_published_interval(self, reader):
        source_end, energy = read_latest_interval_energy("imperial", end=END)

        self.assertEqual(source_end, START)
        self.assertEqual(energy, 0.42)
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(reader.call_args_list[0].kwargs["start"], START)
        self.assertEqual(reader.call_args_list[0].kwargs["end"], END)
        self.assertEqual(reader.call_args_list[1].kwargs["start"], START - timedelta(minutes=15))
        self.assertEqual(reader.call_args_list[1].kwargs["end"], START)

    @patch(
        "power_reading.service.read_interval_energy",
        side_effect=RuntimeError("HNG/Veltol credentials are missing."),
    )
    def test_service_does_not_mask_authentication_failure(self, reader):
        with self.assertRaisesRegex(RuntimeError, "credentials are missing"):
            read_latest_interval_energy("hng", end=END)
        reader.assert_called_once()

    @patch("power_reading.service.read_latest_interval_energy", return_value=(START, 0.31))
    def test_hng_default_origin_reads_portal_on_demand(self, reader):
        origin, energy = get_latest_hng_forecast_origin(
            now=pd.Timestamp("2026-08-30 12:08", tz="Europe/Bucharest")
        )
        self.assertEqual(origin, pd.Timestamp("2026-08-30 12:00", tz="Europe/Bucharest"))
        self.assertEqual(energy, 0.31)
        self.assertEqual(reader.call_args.args[0], "hng")

    @patch("power_reading.service.read_latest_interval_energy", return_value=(START, 0.22))
    def test_portfolio_default_origin_uses_configured_portal_asset(self, reader):
        origin, energy = get_latest_forecast_origin(
            ANTO_INTRADAY_CONFIG,
            now=pd.Timestamp("2026-08-30 12:08", tz="Europe/Bucharest"),
        )
        self.assertEqual(origin, pd.Timestamp("2026-08-30 12:00", tz="Europe/Bucharest"))
        self.assertEqual(energy, 0.22)
        self.assertEqual(reader.call_args.args[0], "anto")

    @patch(
        "power_reading.service.read_latest_interval_energy",
        return_value=(START - timedelta(minutes=15), 0.22),
    )
    def test_portfolio_uses_delayed_source_interval_as_forecast_origin(self, reader):
        origin, energy = get_latest_forecast_origin(
            ANTO_INTRADAY_CONFIG,
            now=pd.Timestamp("2026-08-30 12:08", tz="Europe/Bucharest"),
        )

        self.assertEqual(origin, pd.Timestamp("2026-08-30 11:45", tz="Europe/Bucharest"))
        self.assertEqual(energy, 0.22)
        self.assertEqual(reader.call_args.args[0], "anto")


if __name__ == "__main__":
    unittest.main()
