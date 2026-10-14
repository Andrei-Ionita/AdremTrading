"""Discover the Ulmeni plant using Railway-held credentials; redact all errors."""
import runpy
import json
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
namespace = runpy.run_path(str(root / "scripts/fusionsolar_reauthenticate.py"))
namespace["main"].__globals__["VERIFY_SOURCE"] = '''
import json, os, tempfile
from playwright.sync_api import sync_playwright
from power_reading.scrapers.fusionsolar_scraper import FusionSolarScraper
from power_reading.service import _ASSETS, _build_scraper
username = os.environ.get('ULMENI_FUSIONSOLAR_USERNAME')
password = os.environ.get('ULMENI_FUSIONSOLAR_PASSWORD')
try:
    if not username or not password:
        raise RuntimeError('Credentials not active in running deployment')
    scraper = _build_scraper(_ASSETS['ulmeni'], headless=True)
    with sync_playwright() as playwright, tempfile.TemporaryDirectory() as directory:
        context = playwright.chromium.launch_persistent_context(directory, headless=True,
            viewport={'width': 1920, 'height': 1200})
        try:
            page = context.new_page()
            page.set_default_timeout(45000)
            scraper._open_session(context, page, restore_session=False)
            scraper._open_plant_if_needed(page)
            try:
                snapshot = scraper._read_required_pv_output_power(page)
            except RuntimeError:
                body = page.locator('body').inner_text()
                summary = body.replace(username, '[account]').replace(password, '[redacted]')[:5000]
                print(json.dumps({'asset':'ulmeni', 'status':'power_unmatched', 'source':summary}))
                raise
            scraper.save_session(context, page)
            print(json.dumps({'asset':'ulmeni', 'status':'verified',
                'power_mw': snapshot.pv_kw / 1000, 'timestamp_utc':snapshot.timestamp_utc,
                'source':snapshot.raw_excerpt}))
        finally:
            context.close()
except Exception as exc:
    print(json.dumps({'asset':'ulmeni', 'status':'failed', 'stage':'login', 'error':type(exc).__name__}))
    raise SystemExit(1)
'''
sys.argv = [sys.argv[0], '--ssh-target',
            'd4c3b82b-a09a-4d59-b3e7-bca9f2bd6270@ssh.railway.com',
            '--assets', 'ulmeni', '--verify-only', '--timeout', '180']
try:
    result = subprocess.run([shutil.which('railway'), 'variables', '--service',
        'd69a5990-a6ba-4b75-9bc9-6f94b6c4ba3d', '--json'],
        capture_output=True, text=True, check=True, timeout=60)
    variables = json.loads(result.stdout)
    selected = {key: variables[key] for key in (
        'ULMENI_FUSIONSOLAR_USERNAME', 'ULMENI_FUSIONSOLAR_PASSWORD') if key in variables}
    namespace['main'].__globals__['VERIFY_SOURCE'] = (
        'import os\nos.environ.update(' + repr(selected) + ')\n' +
        namespace['main'].__globals__['VERIFY_SOURCE'])
    namespace['main']()
except Exception as exc:
    print(type(exc).__name__)
    raise SystemExit(1)
