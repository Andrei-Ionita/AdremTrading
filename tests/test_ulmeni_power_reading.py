from __future__ import annotations

import base64
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from portfolio_intraday import (
    PortfolioIntradayConfig,
    ULMENI_INTRADAY_CONFIG,
    calculate_interval_energy,
    predict_portfolio_intraday,
)
from power_reading.scrapers.pcsun_scraper import DEFAULT_PCSUN_TAG, PCSunScraper
from power_reading.scrapers.fusionsolar_scraper import FusionSolarScraper
from power_reading import service
from power_reading.service import available_assets, read_asset
from power_reading.worker import _configured_assets


ULMENI_URL = "https://oltenita1.epgr.ro/~ViewOfThings/index.html"


class LegacyWinCCReaderTests(unittest.TestCase):
    def test_default_tag_is_meter_three_phase_active_power_in_kw(self):
        scraper = PCSunScraper(
            ULMENI_URL,
            active_power_tag=None,
            source_name="ulmeni",
        )

        self.assertEqual(
            scraper.active_power_tag,
            '"UMG_SCALE"."UMG512"."Sum; Psum3=P1+P2+P3"',
        )
        self.assertEqual(scraper.active_power_tag, DEFAULT_PCSUN_TAG)

    def test_first_gate_uses_basic_authentication(self):
        scraper = PCSunScraper(
            ULMENI_URL,
            http_username="gate-user",
            http_password="gate-password",
            source_name="ulmeni",
        )

        expected = base64.b64encode(b"gate-user:gate-password").decode()
        self.assertEqual(scraper._headers()["Authorization"], f"Basic {expected}")

    def test_snapshot_is_labelled_as_ulmeni(self):
        scraper = PCSunScraper(
            ULMENI_URL,
            username="operator",
            password="password",
            source_name="ulmeni",
        )
        with (
            patch.object(scraper, "_login", return_value="token"),
            patch.object(scraper, "_read_value", return_value=747.92) as read,
            patch.object(scraper, "_logout") as logout,
        ):
            snapshot = scraper.scrape_once()

        read.assert_called_once_with("token", DEFAULT_PCSUN_TAG)
        logout.assert_called_once_with("token")
        self.assertEqual(snapshot.pv_kw, 747.92)
        self.assertEqual(snapshot.source, "ulmeni-jsonrpc")

    def test_interval_estimate_uses_current_kw_for_one_quarter(self):
        scraper = PCSunScraper(
            ULMENI_URL,
            username="operator",
            password="password",
            source_name="ulmeni",
        )
        start = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)
        with (
            patch.object(scraper, "_login", return_value="token"),
            patch.object(scraper, "_read_value", return_value=800.0),
            patch.object(scraper, "_logout") as logout,
        ):
            energy = scraper.read_interval_energy(
                start=start,
                end=start + timedelta(minutes=15),
            )

        self.assertAlmostEqual(energy, 0.2)
        logout.assert_called_once_with("token")

    def test_live_estimate_drives_a_bounded_portfolio_correction(self):
        scraper = PCSunScraper(
            ULMENI_URL,
            username="operator",
            password="password",
            source_name="ulmeni",
        )
        interval_start = datetime(2026, 8, 31, 7, 45, tzinfo=timezone.utc)
        with (
            patch.object(scraper, "_login", return_value="token"),
            patch.object(scraper, "_read_value", return_value=800.0),
            patch.object(scraper, "_logout"),
        ):
            actual_energy = scraper.read_interval_energy(
                start=interval_start,
                end=interval_start + timedelta(minutes=15),
            )

        origin = pd.Timestamp("2026-08-31 11:00", tz="Europe/Bucharest")
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
                "Prediction": 0.3,
            }
        )
        weather = pd.DataFrame(
            {"period_end": targets.tz_convert("UTC"), "ghi": 100.0}
        )
        config = PortfolioIntradayConfig(
            asset_key="ulmeni",
            display_name="Solar Energy Ulmeni",
            dam_results_path=Path("dam.xlsx"),
            weather_path=Path("weather.csv"),
            intraday_results_path=Path("intraday.xlsx"),
            max_interval_energy_mwh=4.35 / 4,
        )

        result = predict_portfolio_intraday(
            config,
            dam,
            weather,
            origin,
            actual_energy,
        )

        self.assertAlmostEqual(actual_energy, 0.2)
        self.assertEqual(result["Prediction_ID"].iloc[0], 0.2)
        self.assertEqual(result["Correction"].iloc[0], -0.1)
        self.assertTrue((result["Prediction_ID"] >= 0).all())
        self.assertTrue((result["Prediction_ID"] <= 4.35 / 4).all())

    def test_interval_estimate_rejects_invalid_live_power(self):
        scraper = PCSunScraper(
            ULMENI_URL,
            username="operator",
            password="password",
            source_name="ulmeni",
        )
        start = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)
        with (
            patch.object(scraper, "_login", return_value="token"),
            patch.object(scraper, "_read_value", return_value=float("nan")),
            patch.object(scraper, "_logout"),
            self.assertRaisesRegex(RuntimeError, "missing or invalid"),
        ):
            scraper.read_interval_energy(
                start=start,
                end=start + timedelta(minutes=15),
            )


class UlmeniPowerServiceTests(unittest.TestCase):
    def test_ulmeni_defaults_to_fusionsolar(self):
        self.assertEqual(service._ASSETS["ulmeni"].default_url, service.DEFAULT_FUSIONSOLAR_URL)

    def test_ulmeni_is_registered(self):
        self.assertIn("ulmeni", available_assets())

    @patch("power_reading.scrapers.fusionsolar_scraper.FusionSolarScraper")
    def test_ulmeni_uses_only_its_own_credentials(self, scraper_class):
        with tempfile.TemporaryDirectory() as profile_dir, patch.dict(
            os.environ,
            {
                "ULMENI_USERNAME": "ulmeni-operator",
                "ULMENI_PASSWORD": "ulmeni-app-password",
                "ULMENI_HTTP_USERNAME": "ulmeni-gate",
                "ULMENI_HTTP_PASSWORD": "ulmeni-gate-password",
                "ULMENI_URL": ULMENI_URL,
                "ULMENI_FUSIONSOLAR_USERNAME": "fusion-operator",
                "ULMENI_FUSIONSOLAR_PASSWORD": "fusion-password",
                "ULMENI_FUSIONSOLAR_PLANT_NAME": "Test Ulmeni PV Plant",
                "ULMENI_FUSIONSOLAR_REGION_NAME": "region004",
                "PCSUN_USERNAME": "retired-pcsun-operator",
                "PCSUN_PASSWORD": "retired-pcsun-password",
                "POWER_READING_PROFILE_DIR": profile_dir,
            },
            clear=True,
        ):
            service._build_scraper(service._ASSETS["ulmeni"], headless=True)

        kwargs = scraper_class.call_args.kwargs
        self.assertEqual(kwargs["username"], "fusion-operator")
        self.assertEqual(kwargs["password"], "fusion-password")
        self.assertEqual(kwargs["target_url"], service.DEFAULT_FUSIONSOLAR_URL)
        self.assertEqual(kwargs["plant_name"], "Test Ulmeni PV Plant")
        self.assertEqual(kwargs["region_name"], "region004")
        self.assertTrue(kwargs["require_pv_output_power"])
        self.assertNotIn("http_username", kwargs)
        self.assertIsNotNone(kwargs["session_store"])
        self.assertEqual(Path(kwargs["user_data_dir"]).name, "ulmeni_fusionsolar")

    def test_ulmeni_never_falls_back_to_wincc_or_another_fusion_account(self):
        with patch.dict(os.environ, {
            "ULMENI_USERNAME": "retired-user", "ULMENI_PASSWORD": "retired-password",
            "FUSIONSOLAR_USERNAME": "another-user", "FUSIONSOLAR_PASSWORD": "another-password",
        }, clear=True):
            self.assertEqual(service._credentials(service._ASSETS["ulmeni"]), (None, None))

    def test_ulmeni_defaults_to_verified_oltenita_one_plant(self):
        self.assertEqual(service._ASSETS["ulmeni"].default_plant_name, "CEF OLTENITA 1")

    @patch("power_reading.service._build_scraper")
    def test_service_normalizes_ulmeni_kw_to_mw(self, build_scraper):
        build_scraper.return_value.scrape_once.return_value = SimpleNamespace(
            pv_kw=747.92,
            load_kw=None,
            grid_kw=None,
            timestamp_utc="2026-08-12T08:00:00+00:00",
            source="pv-output-power-text",
            raw_excerpt="Test Ulmeni PV Plant: PV output power 747.92 kW",
        )

        reading = read_asset("ulmeni")

        self.assertEqual(reading.asset, "ulmeni")
        self.assertAlmostEqual(reading.pv_mw, 0.74792)
        self.assertEqual(reading.source, "pv-output-power-text")


class UlmeniCollectionGuardTests(unittest.TestCase):
    def test_default_schedule_excludes_ulmeni_before_live_validation(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("ulmeni", _configured_assets())

    def test_ulmeni_is_not_scheduled_before_live_validation(self):
        with patch.dict(
            os.environ,
            {"POWER_READING_ASSETS": "hng,ulmeni"},
            clear=True,
        ):
            self.assertEqual(_configured_assets(), ["hng"])

    def test_ulmeni_can_be_scheduled_after_live_validation(self):
        with patch.dict(
            os.environ,
            {
                "POWER_READING_ASSETS": "hng,ulmeni",
                "ULMENI_ENABLED": "true",
            },
            clear=True,
        ):
            self.assertEqual(_configured_assets(), ["hng", "ulmeni"])


class UlmeniFusionPowerTests(unittest.TestCase):
    def test_pv_output_units_zero_and_invalid_values(self):
        scraper = FusionSolarScraper(service.DEFAULT_FUSIONSOLAR_URL,
                                     plant_name="Test Ulmeni PV Plant", require_pv_output_power=True)
        for value, expected in (("1.2 MW", 1200), ("800 kW", 800), ("900 W", 0.9), ("0 kW", 0)):
            with self.subTest(value=value):
                page = Mock()
                page.locator.return_value.first.inner_text.return_value = (
                    "Test Ulmeni PV Plant\nGrid 999 kW\nPV\nOutput power\nPV\n" + value)
                snapshot = scraper._read_required_pv_output_power(page)
                self.assertEqual(snapshot.pv_kw, expected)
                self.assertEqual(snapshot.source, "pv-output-power-text")
        for body in ("Test Ulmeni PV Plant PV Output power -- kW Grid 800 kW",
                     "Other Plant PV Output power 800 kW",
                     "Test Ulmeni PV Plant PV Output power 800 kWh",
                     "Test Ulmeni PV Plant PV Output power -1 kW"):
            with self.subTest(body=body):
                page = Mock()
                page.locator.return_value.first.inner_text.return_value = body
                with self.assertRaisesRegex(RuntimeError, "unavailable"):
                    scraper._read_required_pv_output_power(page)

    def test_fusion_samples_drive_ulmeni_correction_with_existing_gap_guard(self):
        origin = pd.Timestamp("2026-10-13 12:00", tz="Europe/Bucharest")
        targets = pd.date_range(origin + pd.Timedelta(minutes=15),
                                origin.normalize() + pd.Timedelta(hours=23, minutes=45), freq="15min")
        weather = pd.DataFrame({"period_end": targets.tz_convert("UTC"), "ghi": 100})
        with patch("power_reading.service._build_scraper") as build:
            readings = []
            for minute in (45, 48, 51, 54, 57):
                stamp = origin.replace(hour=11, minute=minute).tz_convert("UTC").isoformat()
                build.return_value.scrape_once.return_value = SimpleNamespace(
                    pv_kw=800, load_kw=None, grid_kw=None, timestamp_utc=stamp,
                    source="pv-output-power-text", raw_excerpt="")
                readings.append(read_asset("ulmeni"))
        energy = calculate_interval_energy(ULMENI_INTRADAY_CONFIG, readings,
                                           origin - pd.Timedelta(minutes=15), origin)
        self.assertAlmostEqual(energy, 0.2)
        for baseline, expected in ((0.3, 0.2), (0.1, 0.2), (0.5, 0.5)):
            with self.subTest(baseline=baseline):
                dam = pd.DataFrame({"Data": targets.tz_localize(None),
                                    "Interval": targets.hour * 4 + targets.minute // 15 + 1,
                                    "Prediction": baseline})
                result = predict_portfolio_intraday(ULMENI_INTRADAY_CONFIG, dam, weather, origin, energy)
                self.assertAlmostEqual(result["Prediction_ID"].iloc[0], expected)
                self.assertTrue((result["Prediction_ID"] <= 4.35 / 4).all())


if __name__ == "__main__":
    unittest.main()
