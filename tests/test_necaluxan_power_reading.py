from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from power_reading.scrapers.necaluxan_scraper import (
    NecaluxanScraper,
    _unique_visible_locator,
    extract_actual_power_kw,
    select_stable_power_sample,
)
from power_reading.service import available_assets, read_asset


class NecaluxanPowerParserTests(unittest.TestCase):
    def test_reads_only_actual_power_panel_value(self):
        text = """
        31.59 MWp
        Grid connection point 10.8 MW
        Actual power
        17.64 MW
        Janitza U...70158139
        0 W
        30.13 MW
        """
        self.assertEqual(extract_actual_power_kw(text), 17_640.0)

    def test_normalizes_supported_units_to_kw(self):
        self.assertEqual(extract_actual_power_kw("Actual power\n850 kW"), 850.0)
        self.assertEqual(extract_actual_power_kw("Actual power\n125000 W"), 125.0)
        self.assertEqual(extract_actual_power_kw("Actual power\n17,48 MW"), 17_480.0)

    def test_rejects_missing_or_ambiguous_actual_power(self):
        self.assertIsNone(extract_actual_power_kw("Installed PV power 31.59 MWp"))
        self.assertIsNone(
            extract_actual_power_kw("Actual power 1 MW\nActual power 2 MW")
        )

    def test_rejects_transient_dashboard_animation(self):
        observations = [
            (410.0, "410 kW"),
            (3_800.0, "3.80 MW"),
            (12_660.0, "12.66 MW"),
        ]
        self.assertIsNone(select_stable_power_sample(observations))

    def test_accepts_latest_of_three_stable_samples(self):
        observations = [
            (17_600.0, "17.60 MW"),
            (17_640.0, "17.64 MW"),
            (17_680.0, "17.68 MW"),
        ]
        self.assertEqual(
            select_stable_power_sample(observations),
            (17_680.0, "17.68 MW"),
        )

    def test_cockpit_selector_uses_only_a_unique_visible_link(self):
        hidden = _FakeItem(False)
        first_visible = _FakeItem(True)
        second_visible = _FakeItem(True)
        unique_visible = _FakeItem(True)

        selected = _unique_visible_locator(
            (
                _FakeLocator([hidden]),
                _FakeLocator([first_visible, second_visible]),
                _FakeLocator([hidden, unique_visible]),
            )
        )

        self.assertIs(selected, unique_visible)


class NecaluxanPowerServiceTests(unittest.TestCase):
    def test_necaluxan_is_registered(self):
        self.assertIn("necaluxan", available_assets())

    @patch("power_reading.service._build_scraper")
    def test_service_normalizes_necaluxan_kw_to_mw(self, build_scraper):
        build_scraper.return_value.scrape_once.return_value = SimpleNamespace(
            pv_kw=17_640.0,
            load_kw=None,
            grid_kw=None,
            timestamp_utc="2026-08-07T12:00:00+00:00",
            source="meteocontrol-bluelog-actual-power",
            raw_excerpt="Actual power 17.64 MW",
        )

        reading = read_asset("necaluxan")

        self.assertEqual(reading.pv_mw, 17.64)
        self.assertEqual(reading.asset, "necaluxan")

    @patch("power_reading.scrapers.necaluxan_scraper.sync_playwright")
    def test_live_read_uses_a_clean_browser_context(self, sync_playwright):
        playwright = Mock()
        sync_playwright.return_value.__enter__.return_value = playwright
        browser = playwright.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        master_page = Mock()
        scraper = NecaluxanScraper(
            target_url="https://example.test/vcom/",
            username="portal-user",
            password="portal-password",
            master_username="master-user",
            master_password="master-password",
            headless=True,
        )

        with (
            patch.object(scraper, "_login_vcom"),
            patch.object(scraper, "_open_plant_cockpit"),
            patch.object(scraper, "_open_power_control"),
            patch.object(scraper, "_open_bluelog_master", return_value=master_page),
            patch.object(scraper, "_login_bluelog"),
            patch.object(scraper, "_read_actual_power", return_value=(17_640.0, "17.64 MW")),
        ):
            reading = scraper.scrape_once()

        playwright.chromium.launch.assert_called_once()
        playwright.chromium.launch_persistent_context.assert_not_called()
        browser.new_context.assert_called_once()
        context.close.assert_called_once()
        browser.close.assert_called_once()
        self.assertEqual(reading.pv_kw, 17_640.0)
        page.goto.assert_called_once_with(
            "https://example.test/vcom/", wait_until="domcontentloaded"
        )


class _FakeItem:
    def __init__(self, visible):
        self.visible = visible

    def is_visible(self):
        return self.visible


class _FakeLocator:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


if __name__ == "__main__":
    unittest.main()
