from __future__ import annotations

import unittest

from power_reading.scrapers.imperial_scraper import (
    _extract_latest_common_power_mw,
    _extract_latest_power_mw,
)


def imperial_csv(*rows: tuple[str, str]) -> str:
    body = "\n".join(f"{timestamp},{power_w}" for timestamp, power_w in rows)
    return f"Plant export\nTimestamp,Generated Power\n{body}\n"


class ImperialPowerReadingTests(unittest.TestCase):
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
