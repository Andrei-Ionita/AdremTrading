import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from power_reading.fusionsolar_session import (
    FusionSolarSessionStore, is_portal_url, restore_portal_url, session_portal_url,
)
from power_reading.scrapers.fusionsolar_scraper import FusionSolarScraper


URL = 'https://eu5.fusionsolar.huawei.com/uniportal/pvmswebsite/assets/build/cloud.html#/home/list'
STATE = {'url': URL, 'storage_state': {'cookies': [
    {'name': 'session', 'value': 'private-session-value', 'domain': '.fusionsolar.huawei.com', 'path': '/'}
], 'origins': []}}
SESSION = {'origin': 'https://eu5.fusionsolar.huawei.com',
           'items': [{'name': 'auth-state', 'value': 'private-tab-state'}]}
FULL_STATE = {**STATE, 'session_storage': [SESSION]}
ROUTED_URL = URL.replace('#', '?app-id=smartpvms&instance-id=smartpvms&zone-id=region004#')


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

    def test_legacy_empty_query_does_not_use_python310_strict_parser(self):
        with patch('power_reading.fusionsolar_session.parse_qsl', side_effect=ValueError('bad query field')) as parse:
            self.assertTrue(is_portal_url(URL))
        parse.assert_not_called()

    def test_routing_survives_encrypted_round_trip(self):
        state = {**FULL_STATE, 'url': ROUTED_URL}
        self.assertEqual(self.store().decode(self.store().encode(state)), state)

    def test_only_application_routing_is_saved_not_login_tokens(self):
        with_token = ROUTED_URL.replace('#', '&ticket=private-login-token#')
        self.assertEqual(session_portal_url(with_token), ROUTED_URL)
        self.assertFalse(is_portal_url(with_token))
        with self.assertRaises(ValueError):
            self.store().encode({**STATE, 'url': with_token})

    def test_malformed_or_duplicate_routing_is_rejected(self):
        for query in ('app-id=smartpvms&app-id=other', 'app-id=', 'zone-id=%2F%2Fevil.test',
                      'app-id', 'instance-id=' + 'x' * 81):
            self.assertFalse(is_portal_url(URL.replace('#', '?' + query + '#')))
        self.assertFalse(is_portal_url(URL.replace('eu5.', 'eu5.:bad@')))

    def test_legacy_elnet_snapshot_uses_configured_routing(self):
        self.assertEqual(restore_portal_url(URL, ROUTED_URL, None), ROUTED_URL)

    def test_legacy_horeco_snapshot_uses_explicit_region_not_another_host(self):
        self.assertEqual(restore_portal_url(
            URL, 'https://eu5.fusionsolar.huawei.com/unisso/login.action', 'region004'), ROUTED_URL)
        foreign_route = ROUTED_URL.replace('eu5.', 'eu3.')
        self.assertEqual(restore_portal_url(URL, foreign_route, None), URL)

    def test_saved_authenticated_region_takes_precedence_over_login_default(self):
        self.assertEqual(restore_portal_url(ROUTED_URL, URL, 'region003'), ROUTED_URL)

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
        page.evaluate.return_value = SESSION
        context = Mock()
        context.storage_state.return_value = STATE['storage_state']
        scraper = FusionSolarScraper('https://eu3.fusionsolar.huawei.com/unisso/login.action', session_store=store)
        with patch.object(scraper, '_maybe_login') as login:
            scraper._open_session(context, page)
        page.goto.assert_called_once_with(
            'https://eu5.fusionsolar.huawei.com/unisso/login.action', wait_until='domcontentloaded')
        context.add_cookies.assert_called_once_with(STATE['storage_state']['cookies'])
        store.save.assert_called_once_with(FULL_STATE)
        login.assert_called_once_with(page)
        script = context.add_init_script.call_args.args[0]
        self.assertIn('if (sessionStorage.getItem(marker)) return;', script)
        self.assertLess(script.index('sessionStorage.setItem'), script.index('localStorage.setItem'))

    def test_interactive_reauthentication_does_not_import_stale_state(self):
        store = Mock()
        page = Mock(url=URL)
        page.evaluate.return_value = SESSION
        context = Mock()
        context.storage_state.return_value = STATE['storage_state']
        scraper = FusionSolarScraper(URL, session_store=store)
        with patch.object(scraper, '_maybe_login'), patch.object(scraper, '_wait_for_plant_list'):
            scraper._open_session(context, page, restore_session=False)
        store.load.assert_not_called()
        context.add_cookies.assert_not_called()
        context.add_init_script.assert_not_called()
        store.save.assert_called_once_with(FULL_STATE)

    def test_complete_browser_state_restores_tab_auth_before_opening_plant(self):
        store = Mock()
        routed_state = {**FULL_STATE, 'url': ROUTED_URL}
        store.load.return_value = routed_state
        page = Mock(url=ROUTED_URL)
        page.evaluate.return_value = SESSION
        context = Mock()
        context.storage_state.return_value = STATE['storage_state']
        scraper = FusionSolarScraper('https://eu3.fusionsolar.huawei.com/unisso/login.action', session_store=store)
        with patch.object(scraper, '_maybe_login'), patch.object(scraper, '_wait_for_plant_list'):
            scraper._open_session(context, page)
        page.goto.assert_called_once_with(ROUTED_URL, wait_until='domcontentloaded')
        script = context.add_init_script.call_args.args[0]
        self.assertIn('private-tab-state', script)
        self.assertIn('sessionStorage.setItem(item.name, item.value)', script)
        store.save.assert_called_once_with(routed_state)

    def test_legacy_snapshot_is_migrated_before_navigation_and_persisted(self):
        store = Mock()
        store.load.return_value = FULL_STATE
        page = Mock(url=ROUTED_URL)
        page.evaluate.return_value = SESSION
        context = Mock()
        context.storage_state.return_value = STATE['storage_state']
        scraper = FusionSolarScraper(ROUTED_URL, session_store=store)
        with patch.object(scraper, '_maybe_login'), patch.object(scraper, '_wait_for_plant_list'):
            scraper._open_session(context, page)
        page.goto.assert_called_once_with(ROUTED_URL, wait_until='domcontentloaded')
        store.save.assert_called_once_with({**FULL_STATE, 'url': ROUTED_URL})

    def test_tab_auth_is_encrypted_and_origin_restricted(self):
        store = self.store()
        value = store.encode(FULL_STATE)
        self.assertNotIn('private-tab-state', value)
        self.assertEqual(store.decode(value), FULL_STATE)
        bad = copy.deepcopy(FULL_STATE)
        bad['session_storage'][0]['origin'] = 'https://attacker.test'
        with self.assertRaises(ValueError):
            store.encode(bad)

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
