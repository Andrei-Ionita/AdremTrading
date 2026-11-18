import subprocess
import sys
import unittest
from pathlib import Path


class ForecastVerifierTests(unittest.TestCase):
    def test_lazy_reader_import_survives_temporary_forecast_directory(self):
        root = Path(__file__).resolve().parents[1]
        program = (
            'import os,tempfile,importlib.util\n'
            'import scripts.verify_portfolio_forecast\n'
            'with tempfile.TemporaryDirectory() as folder:\n'
            ' os.chdir(folder)\n'
            ' assert importlib.util.find_spec("power_reading.database") is not None\n'
            ' os.chdir(' + repr(str(root)) + ')\n'
        )
        result = subprocess.run([sys.executable, '-B', '-c', program], cwd=root,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_exposes_fresh_weather_and_strict_verification(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-B', 'scripts/verify_portfolio_forecast.py', '--help'],
                                cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--refresh-weather', result.stdout)
        self.assertIn('--require-all-corrections', result.stdout)
