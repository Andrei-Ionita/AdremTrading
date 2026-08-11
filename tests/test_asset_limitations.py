import os
import unittest
from unittest.mock import patch

import database


class AssetLimitationsFeatureGateTests(unittest.TestCase):
    def test_asset_limitations_are_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(database.asset_limitations_enabled())

    def test_disabled_renderer_does_not_access_ui_or_database(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(database.st, "subheader") as subheader,
            patch.object(database, "load_data") as load_data,
        ):
            result = database.render_indisponibility_db(
                "indisponibility_hng",
                "HNG",
            )

        self.assertEqual(result, (None, None, None))
        subheader.assert_not_called()
        load_data.assert_not_called()

    def test_feature_can_be_reenabled_later(self):
        for value in ("1", "true", "YES", "On"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"ASSET_LIMITATIONS_ENABLED": value},
                clear=True,
            ):
                self.assertTrue(database.asset_limitations_enabled())


if __name__ == "__main__":
    unittest.main()
