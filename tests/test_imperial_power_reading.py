from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from power_reading.service import _ASSETS, _build_scraper, _to_mw
from power_reading.scrapers.imperial_scraper import (
    ImperialScraper,
    _extract_latest_common_power_mw,
    _extract_latest_power_mw,
    _parse_chart_power_tooltip,
    _is_recent_enough,
    _parse_number,
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
        self.assertEqual(astro.user_data_dir, imperial.user_data_dir)

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

    def test_latest_missing_csv_sample_does_not_carry_forward(self):
        for missing in ("", "--", "N/A", "NaN", "inf", "-inf"):
            with self.subTest(missing=missing):
                csv_text = imperial_csv(
                    ("2026-08-08T08:00:00Z", "1200000"),
                    ("2026-08-08T08:15:00Z", missing),
                )
                self.assertEqual(_extract_latest_power_mw(csv_text), (None, "2026-08-08T08:15:00Z"))
                self.assertIsNone(_extract_latest_common_power_mw(csv_text, csv_text))

    def test_truncated_latest_csv_row_remains_missing(self):
        csv_text = imperial_csv(("2026-08-08T08:00:00Z", "1200000"))
        csv_text += "2026-08-08T08:15:00Z\n"
        self.assertEqual(_extract_latest_power_mw(csv_text), (None, "2026-08-08T08:15:00Z"))

    def test_csv_preserves_legacy_second_column_watts_convention(self):
        for header in ("Generated Power", "Energy Produced"):
            with self.subTest(header=header):
                csv_text = f"Timestamp,{header}\n2026-08-08T08:00:00Z,1200000\n"
                self.assertEqual(_extract_latest_power_mw(csv_text)[0], 1.2)

    def test_latest_explicit_zero_is_valid(self):
        self.assertEqual(_extract_latest_power_mw(imperial_csv(
            ("2026-08-08T08:00:00Z", "1200000"),
            ("2026-08-08T08:15:00Z", "0"),
        )), (0.0, "2026-08-08T08:15:00Z"))

    def test_numeric_formats_and_nonfinite_values(self):
        for text in ("1,234.50", "1234,50", "1 234.50"):
            self.assertEqual(_parse_number(text), 1234.5)
        for text in ("NaN", "inf", "-inf", "1e309", "--", ""):
            self.assertIsNone(_parse_number(text))

    def test_freshness_handles_local_timestamps_and_rejects_future(self):
        with patch("power_reading.scrapers.imperial_scraper.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 18, 12, 7, tzinfo=timezone.utc)
            self.assertTrue(_is_recent_enough("2026-11-18T14:00:00"))
            self.assertTrue(_is_recent_enough("2026-11-18T12:00:00Z"))
            self.assertFalse(_is_recent_enough("2026-11-18T06:31:00"))
            self.assertFalse(_is_recent_enough("2026-11-18T15:00:00"))
            self.assertFalse(_is_recent_enough(None))

    def test_empty_csv_rechecks_visible_power_and_returns_real_zero(self):
        scraper = ImperialScraper(target_url="https://example.test")
        now = datetime.now(timezone.utc).isoformat()
        with (
            patch.object(scraper, "_download_csv_text", return_value=imperial_csv((now, "--"))),
            patch.object(scraper, "_extract_visible_power_sample", side_effect=[(None, None), (0.0, now)]),
        ):
            self.assertEqual(scraper._download_fresh_csv_snapshot(MagicMock()), ("visible-power:0.0", 0.0, now))

    def test_csv_retry_never_returns_missing_or_stale_value(self):
        scraper = ImperialScraper(target_url="https://example.test")
        now = datetime.now(timezone.utc)
        for csv_text in (
            imperial_csv((now.isoformat(), "--")),
            imperial_csv(((now - timedelta(hours=4)).isoformat(), "1200000")),
        ):
            with (
                self.subTest(csv_text=csv_text),
                patch.object(scraper, "_download_csv_text", return_value=csv_text) as download,
                patch.object(scraper, "_extract_visible_power_sample", return_value=(None, None)),
            ):
                with self.assertRaisesRegex(RuntimeError, "no current numeric power"):
                    scraper._download_fresh_csv_snapshot(MagicMock())
                self.assertEqual(download.call_count, 6)

    def test_fresh_csv_is_used_when_visible_power_is_missing(self):
        scraper = ImperialScraper(target_url="https://example.test")
        now = datetime.now(timezone.utc).isoformat()
        csv_text = imperial_csv((now, "1200000"))
        with (
            patch.object(scraper, "_download_csv_text", return_value=csv_text),
            patch.object(scraper, "_extract_visible_power_sample", return_value=(None, None)),
        ):
            self.assertEqual(scraper._download_fresh_csv_snapshot(MagicMock()), (csv_text, 1.2, now))

    def test_current_visible_power_takes_priority_over_numeric_csv(self):
        scraper = ImperialScraper(target_url="https://example.test")
        now = datetime.now(timezone.utc).isoformat()
        with (
            patch.object(scraper, "_download_csv_text", return_value=imperial_csv((now, "1200000"))),
            patch.object(scraper, "_extract_visible_power_sample", return_value=(1.4, now)),
        ):
            self.assertEqual(scraper._download_fresh_csv_snapshot(MagicMock()), ("visible-power:1.4", 1.4, now))

    def test_plant_read_uses_visible_zero_without_downloading_csv(self):
        scraper = ImperialScraper(target_url="https://example.test", secondary_plant_name=None)
        now = datetime.now(timezone.utc).isoformat()
        with (
            patch("power_reading.scrapers.imperial_scraper._is_login_screen", return_value=False),
            patch("power_reading.scrapers.imperial_scraper._is_plant_dashboard", return_value=True),
            patch.object(scraper, "_go_home"),
            patch.object(scraper, "_open_target_plant", return_value=True),
            patch.object(scraper, "_open_plant_performance_if_needed"),
            patch.object(scraper, "_set_1d_range"),
            patch.object(scraper, "_extract_visible_power_sample", return_value=(0.0, now)),
            patch.object(scraper, "_download_fresh_csv_snapshot") as download,
        ):
            self.assertEqual(scraper._read_plant_power(MagicMock(), ["PV Luna de Jos"], True), (0.0, now, "visible-power:0.0"))
            download.assert_not_called()

    def test_visible_reader_does_not_retimestamp_chart_or_rated_power(self):
        scraper = ImperialScraper(target_url="https://example.test")
        page = MagicMock()
        page.frames = []
        page.locator.return_value.inner_text.return_value = (
            "Rated Power: 5 MW\n2026-11-18 06:31\nGenerated Power: 123000 W"
        )
        page.locator.return_value.all_text_contents.return_value = []
        with patch.object(scraper, "_extract_chart_power_sample", return_value=(None, None)):
            self.assertEqual(scraper._extract_visible_power_sample(page), (None, None))

    def test_visible_current_power_units_and_zero(self):
        scraper = ImperialScraper(target_url="https://example.test")
        page = MagicMock()
        page.frames = []
        page.locator.return_value.all_text_contents.return_value = []
        for text, expected in (("Current Power: 1200 kW", 1.2), ("Active Power\n1.2 MW", 1.2), ("Current Power: 0 W", 0.0)):
            with self.subTest(text=text):
                page.locator.return_value.inner_text.return_value = text
                self.assertEqual(scraper._extract_visible_power_sample(page)[0], expected)

    def test_chart_reader_rejects_stale_and_untimestamped_tooltips(self):
        scraper = ImperialScraper(target_url="https://example.test")
        page = MagicMock()
        page.locator.return_value.first.count.return_value = 1
        page.evaluate.return_value = [{"x": 900.0, "y": 120.0}]
        page.locator.return_value.all_text_contents.return_value = [
            "2020-01-01 06:31\nGenerated Power: 1000000 W",
            "Generated Power: 2000000 W",
        ]
        self.assertEqual(scraper._extract_chart_power_sample(page), (None, None))

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

        with patch("power_reading.scrapers.imperial_scraper.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 8, 26, 12, 32, tzinfo=timezone.utc)
            self.assertEqual(
                scraper._extract_chart_power_sample(page),
                (0.41, "2026-08-26T15:30:00"),
            )
        page.mouse.move.assert_called_once_with(900.0, 120.0)


if __name__ == "__main__":
    unittest.main()
