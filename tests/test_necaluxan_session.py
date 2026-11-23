import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from power_reading.necaluxan_session import NecaluxanSessionStore, vcom_session_state
from power_reading.scrapers.necaluxan_scraper import NecaluxanScraper


class NecaluxanSessionTests(unittest.TestCase):
    def state(self):
        return {'url': 'https://www1.meteocontrol.de/vcom/', 'storage_state': {
            'cookies': [{'domain': 'www1.meteocontrol.de', 'name': 'PHPSESSID',
                         'value': 'test-session-secret', 'path': '/'}], 'origins': []}}

    def test_encrypted_session_is_bound_to_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            store = NecaluxanSessionStore('test-user', 'test-password', Path(directory))
            encoded = store.encode(self.state())
            self.assertNotIn('test-session-secret', encoded)
            self.assertEqual(store.decode(encoded), self.state())
            other = NecaluxanSessionStore('test-user', 'rotated-password', Path(directory))
            self.assertIsNone(other.decode(encoded))
            other = NecaluxanSessionStore('other-user', 'test-password', Path(directory))
            self.assertIsNone(other.decode(encoded))

    def test_untrusted_destination_and_cookies_are_rejected(self):
        state = self.state()
        state['url'] = 'https://example.test/vcom/'
        with self.assertRaises(ValueError):
            NecaluxanSessionStore._validate(state)
        state = self.state()
        state['storage_state']['cookies'][0]['domain'] = 'evil.meteocontrol.de'
        with self.assertRaises(ValueError):
            NecaluxanSessionStore._validate(state)

    def test_snapshot_excludes_bluelog_and_other_origins(self):
        context = Mock()
        context.storage_state.return_value = {
            'cookies': self.state()['storage_state']['cookies'] + [
                {'domain': 'atlas.sspcdn-a.net', 'name': 'control-session', 'value': 'excluded'}],
            'origins': [{'origin': 'https://auth.meteocontrol.com', 'localStorage': []},
                        {'origin': 'https://atlas.sspcdn-a.net', 'localStorage': []}]}
        state = vcom_session_state(context)
        NecaluxanSessionStore._validate(state)
        self.assertEqual(len(state['storage_state']['cookies']), 1)
        self.assertEqual(len(state['storage_state']['origins']), 1)
        self.assertNotIn('excluded', str(state))

    def test_cache_failure_does_not_prevent_fresh_login(self):
        scraper = NecaluxanScraper('https://www1.meteocontrol.de/vcom/')
        scraper.session_store = Mock()
        scraper.session_store.load.side_effect = RuntimeError('unavailable')
        self.assertIsNone(scraper._load_session())
        scraper.session_store.save.side_effect = RuntimeError('unavailable')
        scraper._save_session(self.state())


if __name__ == '__main__':
    unittest.main()
