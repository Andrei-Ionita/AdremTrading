from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from power_reading.scrapers.adc_monitoring_scraper import (
    _extract_metric_kw, _extract_plant_section, _extract_live_site_power_kw,
    _extract_live_card_power_kw,
)
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
    def test_live_api_uses_exact_site_current_power_not_average(self):
        payload = {'groups': [{'sites': [
            {'name': 'CEF Anto Extension', 'pvPowerKw': 900},
            {'name': 'CEF Anto', 'pvPowerKw': 13.199, 'pvPowerAvg15MinKw': 20},
        ]}]}
        self.assertEqual(_extract_live_site_power_kw(payload, 'CEF Anto'), 13.199)

    def test_missing_live_value_never_uses_average(self):
        for value in (None, float('nan'), float('inf'), -1, True):
            with self.subTest(value=value):
                payload = {'groups': [{'sites': [{'name': 'CEF Anto', 'pvPowerKw': value,
                                                 'pvPowerAvg15MinKw': 50}]}]}
                with self.assertRaises(RuntimeError):
                    _extract_live_site_power_kw(payload, 'CEF Anto')

    def test_offline_api_value_rejected_and_live_zero_accepted(self):
        site = {'name': 'CEF Anto', 'pvPowerKw': 0, 'onlineStatus': 'online'}
        payload = {'groups': [{'sites': [site]}]}
        self.assertEqual(_extract_live_site_power_kw(payload, 'CEF Anto'), 0)
        site['onlineStatus'] = 'offline'
        with self.assertRaises(RuntimeError):
            _extract_live_site_power_kw(payload, 'CEF Anto')

    def test_missing_or_duplicate_site_rejected(self):
        for sites in ([], [{'name': 'CEF Anto', 'pvPowerKw': 1}] * 2):
            with self.assertRaises(RuntimeError):
                _extract_live_site_power_kw({'groups': [{'sites': sites}]}, 'CEF Anto')

    def test_card_missing_power_does_not_consume_setpoint_or_next_plant(self):
        text = 'CEF Anto\nLIVE\nCURRENT POWER\n-\nSETPOINT\n1 MW\nPOWER OUTPUT\n-\nCEF Borcea\nCURRENT POWER\n20 kW'
        section = _extract_plant_section(text, 'CEF Anto')
        self.assertNotIn('Borcea', section)
        self.assertIsNone(_extract_live_card_power_kw(section))

    def test_card_communication_failure_never_becomes_zero(self):
        with self.assertRaises(RuntimeError):
            _extract_live_card_power_kw('COMM FAILURE\nPOWER OUTPUT\n0.00 kW')

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

    def test_incuba_uses_adc_plant_and_shared_adc_credentials(self):
        spec = _ASSETS["incuba"]
        environment = {
            "ANTO_USERNAME": "adc-user",
            "ANTO_PASSWORD": "adc-password",
            "INCUBA_USERNAME": "old-fusionsolar-user",
            "INCUBA_PASSWORD": "old-fusionsolar-password",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "power_reading.service._profile_dir",
            return_value=Path(".playwright_profiles/incuba_adc"),
        ):
            scraper = _build_scraper(spec, headless=True)

        self.assertEqual(type(scraper).__name__, "ADCMonitoringScraper")
        self.assertEqual(scraper.plant_name, "CEF Incuba Reproduction")
        self.assertEqual(scraper.username, "adc-user")
        self.assertEqual(scraper.password, "adc-password")

    def test_adc_assets_share_one_browser_profile(self):
        profiles = []
        environment = {"ANTO_USERNAME": "adc-user", "ANTO_PASSWORD": "adc-password"}

        def profile_dir(profile_key):
            profiles.append(profile_key)
            return Path(f".playwright_profiles/{profile_key}")

        with patch.dict(os.environ, environment, clear=True), patch(
            "power_reading.service._profile_dir", side_effect=profile_dir
        ):
            _build_scraper(_ASSETS["anto"], headless=True)
            _build_scraper(_ASSETS["incuba"], headless=True)
            _build_scraper(_ASSETS["ferma_frumusica"], headless=True)
            _build_scraper(_ASSETS["start_fotovoltaice"], headless=True)
            _build_scraper(_ASSETS["renewable_energy_holding"], headless=True)

        self.assertEqual(profiles, ["adc_monitoring"] * 5)

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
