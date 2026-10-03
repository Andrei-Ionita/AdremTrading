import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from power_reading.fusionsolar_session import FusionSolarSessionStore, is_portal_url
from power_reading.scrapers.fusionsolar_scraper import FusionSolarScraper


URL = 'https://eu5.fusionsolar.huawei.com/uniportal/pvmswebsite/assets/build/cloud.html#/home/list'
STATE = {'url': URL, 'storage_state': {'cookies': [
    {'name': 'session', 'value': 'private-session-value', 'domain': '.fusionsolar.huawei.com', 'path': '/'}
], 'origins': []}}


class FusionSolarSessionTests(unittest.TestCase):
    def store(self, asset='elnet', password='test-password', path=Path('.')):
        return FusionSolarSessionStore(asset, 'test-user', password, path)

    def test_encrypted_round_trip(self):
        value = self.store().encode(STATE)
        self.assertNotIn('private-session-value', value)
        self.assertNotIn('test-password', value)
        self.assertEqual(self.store().decode(value), STATE)

    def test_password_rotation_and_other_asset_cannot_restore_session(self):
        value = self.store().encode(STATE)
        self.assertIsNone(self.store(password='rotated').decode(value))
        self.assertIsNone(self.store(asset='horeco').decode(value))

    def test_corrupt_payload_requires_new_login(self):
        self.assertIsNone(self.store().decode('not a session'))

    def test_untrusted_destination_is_rejected(self):
        for url in ('https://attacker.test/uniportal/pvmswebsite/cloud.html#/home/list',
                    URL.replace('https:', 'http:'), URL.replace('eu5.', 'user:password@eu5.'),
                    'https://eu5.fusionsolar.huawei.com/unisso/login.action'):
            with self.subTest(url=url):
                self.assertFalse(is_portal_url(url))
                with self.assertRaises(ValueError):
                    self.store().encode({**STATE, 'url': url})

    def test_unrelated_cookie_domain_is_rejected(self):
        state = copy.deepcopy(STATE)
        state['storage_state']['cookies'][0]['domain'] = 'attacker.test'
        with self.assertRaises(ValueError):
            self.store().encode(state)

    def test_local_fallback_contains_only_ciphertext(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.store(path=Path(directory))
            with patch.object(store, '_uses_database', return_value=False):
                self.assertIsNone(store.load())
                store.save(STATE)
                self.assertEqual(store.load(), STATE)
                self.assertNotIn('private-session-value', store.path.read_text())

    def test_saved_session_initializes_sso_on_authenticated_regional_host(self):
        store = Mock()
        store.load.return_value = STATE
        page = Mock(url=URL)
        context = Mock()
        context.storage_state.return_value = STATE['storage_state']
        scraper = FusionSolarScraper('https://eu3.fusionsolar.huawei.com/unisso/login.action', session_store=store)
        with patch.object(scraper, '_maybe_login') as login:
            scraper._open_session(context, page)
        page.goto.assert_called_once_with(
            'https://eu5.fusionsolar.huawei.com/unisso/login.action', wait_until='domcontentloaded')
        context.add_cookies.assert_called_once_with(STATE['storage_state']['cookies'])
        store.save.assert_called_once_with(STATE)
        login.assert_called_once_with(page)
        script = context.add_init_script.call_args.args[0]
        self.assertIn('if (sessionStorage.getItem(marker)) return;', script)
        self.assertLess(script.index('sessionStorage.setItem'), script.index('localStorage.setItem'))

    def test_interactive_reauthentication_does_not_import_stale_state(self):
        store = Mock()
        page = Mock(url=URL)
        context = Mock()
        context.storage_state.return_value = STATE['storage_state']
        scraper = FusionSolarScraper(URL, session_store=store)
        with patch.object(scraper, '_maybe_login'), patch.object(scraper, '_wait_for_plant_list'):
            scraper._open_session(context, page, restore_session=False)
        store.load.assert_not_called()
        context.add_cookies.assert_not_called()
        context.add_init_script.assert_not_called()
        store.save.assert_called_once_with(STATE)

    def test_unauthenticated_session_is_not_persisted(self):
        store = Mock()
        store.load.return_value = None
        scraper = FusionSolarScraper('https://example.test', session_store=store)
        with patch.object(scraper, '_maybe_login', side_effect=RuntimeError('verification')):
            with self.assertRaises(RuntimeError):
                scraper._open_session(Mock(), Mock())
        store.save.assert_not_called()

    def test_database_save_is_encrypted_and_scoped_to_asset(self):
        store = self.store()
        scope = MagicMock()
        conn = scope.return_value.__enter__.return_value
        cursor = conn.cursor.return_value.__enter__.return_value
        with patch.object(store, '_uses_database', return_value=True), patch(
            'power_reading.database._connection_scope', scope
        ):
            store.save(STATE)
        query, params = cursor.execute.call_args.args
        self.assertIn('ON CONFLICT (asset)', query)
        self.assertEqual(params[0], 'elnet')
        self.assertNotIn('private-session-value', params[1])
        self.assertEqual(store.decode(params[1]), STATE)
        conn.commit.assert_called_once_with()

    def test_session_is_not_saved_before_plant_data_is_ready(self):
        store = Mock()
        store.load.return_value = None
        scraper = FusionSolarScraper(URL, session_store=store)
        with patch.object(scraper, '_maybe_login'), patch.object(
            scraper, '_wait_for_plant_list', side_effect=RuntimeError('plant not ready')
        ):
            with self.assertRaises(RuntimeError):
                scraper._open_session(Mock(), Mock(url=URL))
        store.save.assert_not_called()

    def test_database_load_uses_only_requested_asset(self):
        store = self.store()
        scope = MagicMock()
        cursor = scope.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (store.encode(STATE),)
        with patch.object(store, '_uses_database', return_value=True), patch(
            'power_reading.database._connection_scope', scope
        ):
            self.assertEqual(store.load(), STATE)
        self.assertEqual(cursor.execute.call_args.args[1], ('elnet',))

    def test_no_database_session_starts_without_cached_state(self):
        store = self.store()
        scope = MagicMock()
        scope.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = None
        with patch.object(store, '_uses_database', return_value=True), patch(
            'power_reading.database._connection_scope', scope
        ):
            self.assertIsNone(store.load())


if __name__ == '__main__':
    unittest.main()
