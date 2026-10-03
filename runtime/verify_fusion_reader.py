"""Verify the normal credential-aware reader in isolated browser profiles."""
import runpy
import sys
from pathlib import Path

namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/fusionsolar_reauthenticate.py'))
namespace['main'].__globals__['VERIFY_SOURCE'] = namespace['VERIFY_SOURCE'].replace(
    'scraper.use_saved_session_only = True', 'scraper.use_saved_session_only = False')
sys.argv = [sys.argv[0], '--ssh-target', 'd4c3b82b-a09a-4d59-b3e7-bca9f2bd6270@ssh.railway.com', '--verify-only']
namespace['main']()
