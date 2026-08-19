from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from power_reading.scrapers.adc_monitoring_scraper import _extract_metric_kw, _extract_plant_section
from power_reading.service import _ASSETS, _build_scraper, _credentials, _url


FLEET_TEXT = """
PV POWER
1750.9 kW / 5260.0 kWp
CEF Anto
LIVE
15M AVG
318.0 kW
SETPOINT
1.00 MW
POWER OUTPUT
340.4 kW / 1.0 MWp
CEF Borcea
COMM FAILURE
15M AVG
-
SETPOINT
-
POWER OUTPUT
0.00 kW / 1.4 MWp
CEF Ferma Frumu\u0219ica
LIVE
15M AVG
1322.5 kW
SETPOINT
2.86 MW
POWER OUTPUT
1.41 MW / 2.9 MWp
"""


class ADCMonitoringTests(unittest.TestCase):
    def test_extracts_selected_plant_power_output_instead_of_fleet_total(self):
        anto = _extract_plant_section(FLEET_TEXT, "CEF Anto")
        ferma = _extract_plant_section(FLEET_TEXT, "CEF Ferma Frumusica")

        self.assertEqual(_extract_metric_kw(anto, "POWER OUTPUT"), 340.4)
        self.assertEqual(_extract_metric_kw(ferma, "POWER OUTPUT"), 1410.0)

    def test_plant_matching_ignores_case_and_diacritics(self):
        section = _extract_plant_section(FLEET_TEXT, "CEF FERMA FRUMUSICA")
        self.assertIn("Frumu\u0219ica", section)

    def test_ferma_uses_adc_scraper_and_anto_credentials_as_fallback(self):
        spec = _ASSETS["ferma_frumusica"]
        environment = {"ANTO_USERNAME": "adc-user", "ANTO_PASSWORD": "adc-password"}
        with patch.dict(os.environ, environment, clear=True), patch(
            "power_reading.service._profile_dir", return_value=Path(".playwright_profiles/ferma_frumusica")
        ):
            scraper = _build_scraper(spec, headless=True)
            self.assertEqual(_url(spec), "https://adc-monitoring.ro/")
            self.assertEqual(_credentials(spec), ("adc-user", "adc-password"))

        self.assertEqual(type(scraper).__name__, "ADCMonitoringScraper")
        self.assertEqual(scraper.username, "adc-user")
        self.assertEqual(scraper.password, "adc-password")

    def test_start_fotovoltaice_uses_borcea_and_shared_adc_credentials(self):
        spec = _ASSETS["start_fotovoltaice"]
        environment = {"ANTO_USERNAME": "adc-user", "ANTO_PASSWORD": "adc-password"}
        with patch.dict(os.environ, environment, clear=True), patch(
            "power_reading.service._profile_dir",
            return_value=Path(".playwright_profiles/start_fotovoltaice"),
        ):
            scraper = _build_scraper(spec, headless=True)

        self.assertEqual(type(scraper).__name__, "ADCMonitoringScraper")
        self.assertEqual(scraper.plant_name, "CEF Borcea")
        self.assertEqual(scraper.username, "adc-user")
        self.assertEqual(scraper.password, "adc-password")
        borcea = _extract_plant_section(FLEET_TEXT, "CEF Borcea")
        self.assertEqual(_extract_metric_kw(borcea, "POWER OUTPUT"), 0.0)


if __name__ == "__main__":
    unittest.main()
