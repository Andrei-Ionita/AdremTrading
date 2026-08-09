from __future__ import annotations

import unittest
from unittest.mock import patch

from balancing import refresh_intraday_corrections


class IntradayRefreshTests(unittest.TestCase):
    def test_failed_refresh_is_disabled_for_the_export(self):
        def fail():
            raise ValueError("missing fresh reading")

        refreshers = (
            ("working", "Working", lambda: "fresh", ValueError),
            ("failed", "Failed", fail, ValueError),
        )

        available, results, errors = refresh_intraday_corrections(refreshers)

        self.assertEqual(available, {"working": True, "failed": False})
        self.assertEqual(results, {"working": "fresh"})
        self.assertEqual(errors, {"Failed": "missing fresh reading"})

    def test_default_refresh_includes_astro(self):
        with (
            patch("balancing.run_portfolio_intraday_forecast", return_value="portfolio"),
            patch("balancing.run_elnet_intraday_forecast", return_value="elnet"),
            patch("balancing.run_horeco_intraday_forecast", return_value="horeco"),
            patch("balancing.run_hng_intraday_forecast", return_value="hng"),
            patch("balancing.run_incuba_intraday_forecast", return_value="incuba"),
        ):
            available, _, errors = refresh_intraday_corrections()

        self.assertEqual(
            set(available),
            {
                "astro",
                "imperial",
                "elnet",
                "horeco",
                "hng",
                "incuba",
                "anto",
                "motif",
                "ferma",
                "necaluxan",
            },
        )
        self.assertEqual(errors, {})


if __name__ == "__main__":
    unittest.main()
