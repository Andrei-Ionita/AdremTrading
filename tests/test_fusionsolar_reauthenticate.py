import ast
import base64
import io
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class FusionSolarReauthenticationTests(unittest.TestCase):
    def test_staged_ulmeni_credentials_travel_only_in_ssh_stdin(self):
        root = Path(__file__).resolve().parents[1]
        helper = runpy.run_path(str(root / 'scripts/fusionsolar_reauthenticate.py'))
        process = Mock(returncode=0)
        process.communicate.return_value = ('', '')
        process.poll.return_value = 0
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as home, patch.object(Path, 'home', return_value=Path(home)), \
                patch('subprocess.run') as run, patch('subprocess.check_output', return_value='256 SHA256:test key'), \
                patch('subprocess.Popen', return_value=process) as popen, \
                patch('shutil.which', return_value='railway'), patch('sys.stdout', output), \
                patch('sys.argv', ['verify', '--ssh-target', 'test@ssh.railway.com',
                                   '--assets', 'ulmeni', '--verify-only']):
            helper['main'](environment_overrides={'ULMENI_FUSIONSOLAR_PASSWORD': 'fixture-secret'})
        self.assertNotIn('fixture-secret', str(popen.call_args))
        self.assertNotIn('fixture-secret', str(run.call_args_list))
        self.assertNotIn('fixture-secret', output.getvalue())
        transport = ast.parse(process.stdin.write.call_args.args[0])
        encoded = transport.body[0].value.args[0].args[0].value
        bootstrap = base64.b64decode(encoded).decode()
        self.assertIn("os.environ.update({'ULMENI_FUSIONSOLAR_PASSWORD': 'fixture-secret'})", bootstrap)
        self.assertIn("VERIFY_ASSETS=['ulmeni']", bootstrap)
        self.assertTrue(any('remove' in call.args[0] for call in run.call_args_list))


if __name__ == '__main__':
    unittest.main()
