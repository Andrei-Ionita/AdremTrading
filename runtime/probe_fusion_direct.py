"""Test saved portal state without revisiting the sign-in endpoint."""
import runpy
import sys
from pathlib import Path

namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/fusionsolar_reauthenticate.py'))
namespace['main'].__globals__['VERIFY_SOURCE'] = """
import json
from power_reading.scrapers.fusionsolar_scraper import FusionSolarScraper
def direct_open(self, context, page):
    state = self.session_store.load()
    context.add_cookies(state['storage_state']['cookies'])
    origins = json.dumps(state['storage_state']['origins'])
    context.add_init_script('''(() => {
        const saved = %s;
        const origin = saved.find(item => item.origin === location.origin);
        if (origin) for (const item of origin.localStorage || [])
            localStorage.setItem(item.name, item.value);
    })();''' % origins)
    page.goto(state['url'], wait_until='domcontentloaded')
    self._wait_for_authenticated_page(page)
    self._wait_for_plant_list(page)
    self.save_session(context, page)
FusionSolarScraper._open_session = direct_open
""" + namespace['VERIFY_SOURCE']
sys.argv = [sys.argv[0], '--ssh-target', 'd4c3b82b-a09a-4d59-b3e7-bca9f2bd6270@ssh.railway.com', '--verify-only']
namespace['main']()
