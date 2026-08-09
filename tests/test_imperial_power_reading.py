from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from power_reading.service import _ASSETS, _build_scraper, _to_mw
from power_reading.scrapers.imperial_scraper import (
    _extract_latest_common_power_mw,
    _extract_latest_power_mw,
)


def imperial_csv(*rows: tuple[str, str]) -> str:
    body = "\n".join(f"{timestamp},{power_w}" for timestamp, power_w in rows)
    return f"Plant export\nTimestamp,Generated Power\n{body}\n"


class ImperialPowerReadingTests(unittest.TestCase):
    def test_aurora_assets_are_independent_single_plant_readers(self):
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
        self.assertIsNone(imperial.secondary_plant_name)
        self.assertEqual(imperial.source_prefix, "imperial")

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


if __name__ == "__main__":
    unittest.main()
