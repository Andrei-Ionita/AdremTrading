from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from power_reading.service import _ASSETS, _build_scraper, _to_mw
from power_reading.scrapers.imperial_scraper import (
    ImperialScraper,
    _extract_latest_common_power_mw,
    _extract_latest_power_mw,
    _parse_chart_power_tooltip,
)


def imperial_csv(*rows: tuple[str, str]) -> str:
    body = "\n".join(f"{timestamp},{power_w}" for timestamp, power_w in rows)
    return f"Plant export\nTimestamp,Generated Power\n{body}\n"


class ImperialPowerReadingTests(unittest.TestCase):
    def test_aurora_assets_use_their_distinct_plant_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "IMPERIAL_USERNAME": "shared-user",
                    "IMPERIAL_PASSWORD": "shared-password",
                    "POWER_READING_PROFILE_DIR": directory,
                },
                clear=True,
            ):
                astro = _build_scraper(_ASSETS["astro"], headless=True)
                imperial = _build_scraper(_ASSETS["imperial"], headless=True)

        self.assertEqual(astro.plant_name, "PV Luna de Jos")
        self.assertEqual(astro.username, "shared-user")
        self.assertIsNone(astro.secondary_plant_name)
        self.assertEqual(astro.source_prefix, "astro-aurora")
        self.assertEqual(imperial.plant_name, "PV Jucu")
        self.assertEqual(imperial.secondary_plant_name, "Imperial 2")
        self.assertEqual(imperial.source_prefix, "imperial")

    def test_imperial_two_aliases_never_include_astro_luna(self):
        from power_reading.scrapers.imperial_scraper import _plant_aliases

        aliases = _plant_aliases("Imperial 2")

        self.assertIn("Imperial 2", aliases)
        self.assertNotIn("PV Luna de Jos", aliases)
        self.assertNotIn("Luna de Jos", aliases)

    def test_aurora_values_are_already_normalized_to_mw(self):
        self.assertEqual(_to_mw(1.7, "astro"), 1.7)
        self.assertEqual(_to_mw(1.2, "imperial"), 1.2)

    def test_latest_single_feed_value_is_converted_from_watts_to_mw(self):
        csv_text = imperial_csv(
            ("2026-08-08 08:00:00.000Z", "1200000"),
            ("2026-08-08 08:15:00.000Z", "1400000"),
        )

        self.assertEqual(
            _extract_latest_power_mw(csv_text),
            (1.4, "2026-08-08 08:15:00.000Z"),
        )

    def test_component_feeds_use_the_latest_common_quarter(self):
        primary = imperial_csv(
            ("2026-08-08 08:00:00.000Z", "1000000"),
        )
        secondary = imperial_csv(
            ("2026-08-08 08:00:00.000Z", "2000000"),
            ("2026-08-08 08:15:00.000Z", "2500000"),
        )

        self.assertEqual(
            _extract_latest_common_power_mw(primary, secondary),
            (1.0, 2.0, "2026-08-08 08:00:00.000Z"),
        )

    def test_component_feeds_without_common_quarter_are_rejected(self):
        primary = imperial_csv(("2026-08-08 08:00:00.000Z", "1000000"))
        secondary = imperial_csv(("2026-08-08 08:15:00.000Z", "2000000"))

        self.assertIsNone(_extract_latest_common_power_mw(primary, secondary))

    def test_reads_current_power_from_aurora_chart_tooltip(self):
        self.assertEqual(
            _parse_chart_power_tooltip(
                "2026-08-26 14:30\nGenerated Power: 2220100.00 W"
            ),
            (2.2201, "2026-08-26T14:30:00"),
        )

    def test_chart_tooltip_normalizes_kw_to_mw(self):
        self.assertEqual(
            _parse_chart_power_tooltip(
                "2026-01-15 09:45\nGenerated Power: 1,234.50 kW"
            ),
            (1.2345, "2026-01-15T09:45:00"),
        )

    def test_ignores_empty_chart_tooltips(self):
        self.assertIsNone(_parse_chart_power_tooltip(None))

    def test_imperial_sums_tooltip_values_and_preserves_both_timestamps(self):
        scraper = ImperialScraper(
            target_url="https://example.test",
            plant_name="PV Jucu",
            secondary_plant_name="Imperial 2",
            headless=True,
        )
        context = MagicMock()
        context.new_page.return_value = MagicMock()
        playwright = MagicMock()
        playwright.chromium.launch_persistent_context.return_value = context
        playwright_manager = MagicMock()
        playwright_manager.__enter__.return_value = playwright

        component_samples = [
            (2.2201, "2026-08-26T14:30:00", "visible-power:2.2201"),
            (1.1254, "2026-08-26T14:31:00", "visible-power:1.1254"),
        ]
        with (
            patch(
                "power_reading.scrapers.imperial_scraper.sync_playwright",
                return_value=playwright_manager,
            ),
            patch.object(scraper, "_goto_with_fallbacks"),
            patch.object(scraper, "_maybe_login"),
            patch.object(scraper, "_go_home"),
            patch.object(scraper, "_read_plant_power", side_effect=component_samples),
        ):
            snapshot = scraper.scrape_once()

        self.assertAlmostEqual(snapshot.pv_kw, 3.3455)
        self.assertEqual(snapshot.load_kw, 2.2201)
        self.assertEqual(snapshot.grid_kw, 1.1254)
        self.assertEqual(
            snapshot.source,
            "imperial-visible-fallback@PV Jucu:2026-08-26T14:30:00|"
            "Imperial 2:2026-08-26T14:31:00",
        )

    def test_chart_reader_uses_rightmost_segment_after_data_gap(self):
        scraper = ImperialScraper(target_url="https://example.test", headless=True)
        page = MagicMock()
        chart = MagicMock()
        chart.count.return_value = 1
        chart_container = MagicMock()
        chart_container.first = chart
        tooltip_locator = MagicMock()
        tooltip_locator.all_text_contents.return_value = [
            "2026-08-26 15:30\nGenerated Power: 410000.00 W"
        ]

        def locate(selector):
            if selector == ".chartdiv":
                return chart_container
            if selector == "[role='tooltip']":
                return tooltip_locator
            return MagicMock()

        page.locator.side_effect = locate
        page.evaluate.return_value = [
            {"x": 900.0, "y": 120.0},
            {"x": 500.0, "y": 140.0},
        ]

        self.assertEqual(
            scraper._extract_chart_power_sample(page),
            (0.41, "2026-08-26T15:30:00"),
        )
        page.mouse.move.assert_called_once_with(900.0, 120.0)


if __name__ == "__main__":
    unittest.main()
