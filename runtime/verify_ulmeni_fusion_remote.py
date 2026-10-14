"""Verify Ulmeni using staged Railway credentials without deploying first."""
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    interactive = '--interactive' in sys.argv
    root = Path(__file__).resolve().parents[1]
    helper = runpy.run_path(str(root / 'scripts/fusionsolar_reauthenticate.py'))
    result = subprocess.run([shutil.which('railway'), 'variables', '--project',
        '119836b2-1c9a-424f-a190-177750a338e4', '--environment',
        '14a08f03-4191-4378-932b-eaf12cc649be', '--service',
        'd69a5990-a6ba-4b75-9bc9-6f94b6c4ba3d', '--json'],
        capture_output=True, text=True, check=True, timeout=60)
    variables = json.loads(result.stdout)
    selected = {key: variables[key] for key in (
        'ULMENI_FUSIONSOLAR_USERNAME', 'ULMENI_FUSIONSOLAR_PASSWORD')}
    sys.argv = [sys.argv[0], '--ssh-target',
        'd4c3b82b-a09a-4d59-b3e7-bca9f2bd6270@ssh.railway.com', '--assets', 'ulmeni']
    if not interactive:
        sys.argv += ['--verify-only', '--timeout', '180']
    helper['main'](environment_overrides=selected)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(type(exc).__name__)
        raise SystemExit(1)
