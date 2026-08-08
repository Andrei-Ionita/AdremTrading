from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
