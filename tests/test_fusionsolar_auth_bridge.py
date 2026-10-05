import io
import unittest
from unittest.mock import MagicMock, Mock, patch

from power_reading.fusionsolar_auth_bridge import VerificationBridge, main


class FusionSolarBridgeTests(unittest.TestCase):
    def test_selected_verification_does_not_touch_other_accounts(self):
        with patch('power_reading.fusionsolar_auth_bridge._connection_scope', MagicMock()):
            bridge = VerificationBridge(Mock(), ['elnet'])
        self.assertEqual(list(bridge.pending), ['elnet'])

    def test_unknown_or_empty_account_selection_is_rejected(self):
        for assets in ([], ['anasun'], ['elnet', 'unrelated'], ['renewable_energy_holding']):
            with self.subTest(assets=assets), self.assertRaises(ValueError):
                VerificationBridge(Mock(), assets)

    def test_invalid_verification_code_is_not_submitted(self):
        bridge = object.__new__(VerificationBridge)
        bridge.asset = 'elnet'
        bridge.page = Mock()
        bridge.status = Mock(return_value={'status': 'verification_required'})
        result = bridge.submit('invalid code with spaces')
        self.assertIn('error', result)
        bridge.page.locator.assert_not_called()

    def test_failed_session_save_does_not_mark_account_complete(self):
        bridge = object.__new__(VerificationBridge)
        bridge.asset = 'elnet'
        bridge.completed = []
        bridge.context = Mock()
        bridge.page = Mock()
        bridge.scraper = Mock()
        bridge.scraper.save_session.side_effect = ValueError('invalid session')
        bridge.next_asset = Mock()
        with self.assertRaises(ValueError):
            bridge.complete_asset()
        self.assertEqual(bridge.completed, [])
        bridge.next_asset.assert_not_called()

    def test_refresh_does_not_skip_unverified_asset(self):
        bridge = Mock(asset='elnet', completed=[])
        bridge.status.return_value = {'asset': 'elnet', 'status': 'verification_required'}
        with patch('power_reading.fusionsolar_auth_bridge.sync_playwright', MagicMock()), patch(
            'power_reading.fusionsolar_auth_bridge.VerificationBridge', return_value=bridge
        ), patch('sys.stdin', io.StringIO('{"command":"start"}\n{"command":"stop"}\n')), patch('sys.stdout', io.StringIO()):
            main()
        bridge.status.assert_called_once_with()
        bridge.next_asset.assert_not_called()
        bridge.close.assert_called_once_with()

    def test_missing_plant_data_does_not_mark_account_complete(self):
        bridge = object.__new__(VerificationBridge)
        bridge.asset = 'horeco'
        bridge.completed = []
        bridge.context = Mock()
        bridge.page = Mock()
        bridge.scraper = Mock()
        bridge.scraper._wait_for_plant_list.side_effect = RuntimeError('not ready')
        bridge.next_asset = Mock()
        with self.assertRaises(RuntimeError):
            bridge.complete_asset()
        bridge.scraper.save_session.assert_not_called()
        bridge.next_asset.assert_not_called()
        self.assertEqual(bridge.completed, [])

    def test_browser_errors_never_echo_credentials(self):
        bridge = Mock(asset=None, completed=[])
        bridge.next_asset.side_effect = RuntimeError('filled secret-password')
        output = io.StringIO()
        with patch('power_reading.fusionsolar_auth_bridge.sync_playwright', MagicMock()), patch(
            'power_reading.fusionsolar_auth_bridge.VerificationBridge', return_value=bridge
        ), patch('sys.stdin', io.StringIO('{"command":"start"}\n{"command":"stop"}\n')), patch('sys.stdout', output):
            main()
        self.assertIn('RuntimeError', output.getvalue())
        self.assertNotIn('secret-password', output.getvalue())

    def test_resume_rechecks_login_without_submitting_verification_again(self):
        bridge = Mock(asset='elnet', completed=[])
        bridge.complete_asset.return_value = {'asset': 'horeco', 'status': 'verification_required'}
        with patch('power_reading.fusionsolar_auth_bridge.sync_playwright', MagicMock()), patch(
            'power_reading.fusionsolar_auth_bridge.VerificationBridge', return_value=bridge
        ), patch('sys.stdin', io.StringIO('{"command":"resume"}\n{"command":"stop"}\n')), patch('sys.stdout', io.StringIO()):
            main()
        bridge.complete_asset.assert_called_once_with()
        bridge.submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
