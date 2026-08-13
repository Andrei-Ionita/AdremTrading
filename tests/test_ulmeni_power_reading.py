from __future__ import annotations

import base64
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from power_reading.scrapers.pcsun_scraper import DEFAULT_PCSUN_TAG, PCSunScraper
from power_reading import service
from power_reading.service import available_assets, read_asset
from power_reading.worker import _configured_assets


ULMENI_URL = "https://oltenita1.epgr.ro/~ViewOfThings/index.html"


class UlmeniJSONRPCReaderTests(unittest.TestCase):
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


class UlmeniPowerServiceTests(unittest.TestCase):
    def test_ulmeni_defaults_to_oltenita_one(self):
        self.assertEqual(service._ASSETS["ulmeni"].default_url, ULMENI_URL)

    def test_ulmeni_is_registered(self):
        self.assertIn("ulmeni", available_assets())

    @patch("power_reading.scrapers.pcsun_scraper.PCSunScraper")
    def test_ulmeni_uses_only_its_own_credentials(self, scraper_class):
        with tempfile.TemporaryDirectory() as profile_dir, patch.dict(
            os.environ,
            {
                "ULMENI_USERNAME": "ulmeni-operator",
                "ULMENI_PASSWORD": "ulmeni-app-password",
                "ULMENI_HTTP_USERNAME": "ulmeni-gate",
                "ULMENI_HTTP_PASSWORD": "ulmeni-gate-password",
                "PCSUN_USERNAME": "retired-pcsun-operator",
                "PCSUN_PASSWORD": "retired-pcsun-password",
                "POWER_READING_PROFILE_DIR": profile_dir,
            },
            clear=True,
        ):
            service._build_scraper(service._ASSETS["ulmeni"], headless=True)

        kwargs = scraper_class.call_args.kwargs
        self.assertEqual(kwargs["username"], "ulmeni-operator")
        self.assertEqual(kwargs["password"], "ulmeni-app-password")
        self.assertEqual(kwargs["http_username"], "ulmeni-gate")
        self.assertEqual(kwargs["http_password"], "ulmeni-gate-password")
        self.assertEqual(kwargs["source_name"], "ulmeni")

    @patch("power_reading.service._build_scraper")
    def test_service_normalizes_ulmeni_kw_to_mw(self, build_scraper):
        build_scraper.return_value.scrape_once.return_value = SimpleNamespace(
            pv_kw=747.92,
            load_kw=None,
            grid_kw=None,
            timestamp_utc="2026-08-12T08:00:00+00:00",
            source="ulmeni-jsonrpc",
            raw_excerpt=f"tag={DEFAULT_PCSUN_TAG} value=747.92",
        )

        reading = read_asset("ulmeni")

        self.assertEqual(reading.asset, "ulmeni")
        self.assertAlmostEqual(reading.pv_mw, 0.74792)
        self.assertEqual(reading.source, "ulmeni-jsonrpc")


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


if __name__ == "__main__":
    unittest.main()
