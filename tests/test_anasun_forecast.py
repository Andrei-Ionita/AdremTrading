from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import joblib
import numpy as np
import pandas as pd

from ml import fetching_AnaSun_data_15min, predicting_exporting_AnaSun_15min


class AnaSunForecastTests(unittest.TestCase):
    def test_model_loads_with_six_features(self):
        model = joblib.load("AnaSun/rs_xgb_anasun_prod_15min_0426.pkl")

        self.assertEqual(model.n_features_in_, 6)

    def test_weather_fetch_uses_anasun_coordinates(self):
        response = Mock(status_code=200, content=b"period_end,ghi\n", text="")
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "Ulmi_15min.csv"
            with patch("ml.requests.get", return_value=response) as request_get:
                fetching_AnaSun_data_15min(output_path)

            requested_url = request_get.call_args.args[0]
            self.assertIn("latitude=44.897116", requested_url)
            self.assertIn("longitude=25.499325", requested_url)
            self.assertEqual(output_path.read_bytes(), response.content)

    def test_prediction_uses_expected_features_and_local_timestamps(self):
        weather = pd.DataFrame(
            {
                "period_end": [
                    "2026-01-15T10:00:00Z",
                    "2026-01-15T10:15:00Z",
                ],
                "air_temp": [20.0, 19.0],
                "cloud_opacity": [30.0, 40.0],
                "ghi": [100.0, 0.0],
            }
        )
        model = Mock()
        model.predict.return_value = np.array([0.25, 0.4])

        with tempfile.TemporaryDirectory() as temp_dir:
            weather_path = Path(temp_dir) / "weather.csv"
            output_path = Path(temp_dir) / "forecast.xlsx"
            weather.to_csv(weather_path, index=False)
            with patch("ml.joblib.load", return_value=model):
                result = predicting_exporting_AnaSun_15min(
                    1,
                    24,
                    0,
                    weather_path=weather_path,
                    model_path="unused.pkl",
                    output_path=output_path,
                )
            self.assertTrue(output_path.is_file())

        np.testing.assert_array_equal(
            model.predict.call_args.args[0],
            np.array(
                [
                    [49.0, 20.0, 30.0, 100.0, 1.0, 0.0],
                    [50.0, 19.0, 40.0, 0.0, 1.0, 1.0],
                ]
            ),
        )
        self.assertEqual(result["Data"].iloc[0], pd.Timestamp("2026-01-15 12:00"))
        self.assertEqual(result["Prediction"].tolist(), [0.25, 0.0])


if __name__ == "__main__":
    unittest.main()
