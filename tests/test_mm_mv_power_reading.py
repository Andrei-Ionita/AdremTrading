from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from power_reading.scrapers.isolarcloud_scraper import (
    ISolarCloudReadOnlyError,
    extract_realtime_power_kw,
)
from power_reading.service import _ASSETS, available_assets, read_asset


HEADERS = [
    "Plant name",
    "Status",
    "Plant type",
    "Installed power",
    "Real-time power",
    "Yield today",
]
CELLS = [
    "Reghin\nGuest\nReghin",
    "Normal",
    "C&I PV",
    "6.00 MWp",
    "4.37 MW",
    "19.37 MWh",
]


class ISolarCloudParserTests(unittest.TestCase):
    def test_reads_only_reghin_realtime_power_column(self):
        power_kw, raw_value = extract_realtime_power_kw(HEADERS, CELLS, "Reghin")
        self.assertEqual(power_kw, 4_370.0)
        self.assertEqual(raw_value, "4.37 MW")

    def test_supports_kw_and_w_values(self):
        kw_cells = [*CELLS]
        kw_cells[4] = "850 kW"
        self.assertEqual(
            extract_realtime_power_kw(HEADERS, kw_cells, "Reghin")[0],
            850.0,
        )
        watt_cells = [*CELLS]
        watt_cells[4] = "125000 W"
        self.assertEqual(
            extract_realtime_power_kw(HEADERS, watt_cells, "Reghin")[0],
            125.0,
        )

    def test_rejects_another_plant_or_missing_power_column(self):
        with self.assertRaisesRegex(ISolarCloudReadOnlyError, "requested plant"):
            extract_realtime_power_kw(HEADERS, CELLS, "Parc Cateasca")
        with self.assertRaisesRegex(ISolarCloudReadOnlyError, "Real-time power column"):
            extract_realtime_power_kw(
                [header for header in HEADERS if header != "Real-time power"],
                CELLS,
                "Reghin",
            )


class MM_MVPowerServiceTests(unittest.TestCase):
    def test_mm_mv_is_registered(self):
        self.assertIn("mm_mv", available_assets())

    @patch("power_reading.service._build_scraper")
    def test_service_normalizes_isolarcloud_kw_to_mw(self, build_scraper):
        build_scraper.return_value.scrape_once.return_value = SimpleNamespace(
            pv_kw=4_370.0,
            load_kw=None,
            grid_kw=None,
            timestamp_utc="2026-08-10T08:00:00+00:00",
            source="isolarcloud-plant-list-real-time-power@Reghin",
            raw_excerpt="Reghin | Real-time power 4.37 MW",
        )

        reading = read_asset("mm_mv")

        self.assertEqual(reading.pv_mw, 4.37)
        self.assertEqual(reading.asset, "mm_mv")


class AnaSunPowerServiceTests(unittest.TestCase):
    def test_anasun_is_registered_for_its_own_isolarcloud_account(self):
        self.assertIn("anasun", available_assets())
        self.assertEqual(_ASSETS["anasun"].default_plant_name, "AnaSun Ulmi")
        self.assertEqual(_ASSETS["anasun"].env_prefix, "ANASUN")
        self.assertNotEqual(_ASSETS["anasun"].asset_type, _ASSETS["mm_mv"].asset_type)

    def test_reads_anasun_realtime_power_column(self):
        cells = [*CELLS]
        cells[0] = "AnaSun Ulmi\nGuest\nAnaSun Ulmi"
        cells[3] = "9.36 MWp"
        cells[4] = "7.6 MW"

        power_kw, raw_value = extract_realtime_power_kw(
            HEADERS, cells, "AnaSun Ulmi"
        )

        self.assertEqual(power_kw, 7_600.0)
        self.assertEqual(raw_value, "7.6 MW")

    @patch("power_reading.service._build_scraper")
    def test_service_normalizes_anasun_kw_to_mw(self, build_scraper):
        build_scraper.return_value.scrape_once.return_value = SimpleNamespace(
            pv_kw=7_600.0,
            load_kw=None,
            grid_kw=None,
            timestamp_utc="2026-08-19T10:00:00+00:00",
            source="isolarcloud-plant-list-real-time-power@AnaSun Ulmi",
            raw_excerpt="AnaSun Ulmi | Real-time power 7.6 MW",
        )

        reading = read_asset("anasun")

        self.assertEqual(reading.pv_mw, 7.6)
        self.assertEqual(reading.asset, "anasun")


if __name__ == "__main__":
    unittest.main()
