"""Account-bound encrypted VCOM sessions, never blue'Log control state."""
from pathlib import Path
from urllib.parse import urlsplit

from .fusionsolar_session import FusionSolarSessionStore


_HOSTS = frozenset(('www1.meteocontrol.de', 'auth.meteocontrol.com'))
_PORTAL = 'https://www1.meteocontrol.de/vcom/'


class NecaluxanSessionStore(FusionSolarSessionStore):
    def __init__(self, username: str, password: str, profile_dir: Path):
        super().__init__('necaluxan', username, password, profile_dir)
        self.path = Path(profile_dir) / 'necaluxan-session.enc'

    @staticmethod
    def _validate(state: dict) -> None:
        if not isinstance(state, dict) or state.get('url') != _PORTAL:
            raise ValueError('Invalid VCOM session destination.')
        storage = state.get('storage_state')
        if not isinstance(storage, dict):
            raise ValueError('Invalid VCOM browser state.')
        if not isinstance(storage.get('cookies'), list) or not isinstance(storage.get('origins'), list):
            raise ValueError('Invalid VCOM browser state.')
        for cookie in storage['cookies']:
            if cookie.get('domain', '').lstrip('.') not in _HOSTS:
                raise ValueError('Unexpected VCOM cookie domain.')
        for origin in storage['origins']:
            parsed = urlsplit(origin.get('origin', ''))
            if parsed.scheme != 'https' or parsed.hostname not in _HOSTS or parsed.port not in (None, 443):
                raise ValueError('Unexpected VCOM storage origin.')
        if state.get('session_storage'):
            raise ValueError('VCOM tab state is not persisted.')


def vcom_session_state(context) -> dict:
    storage = context.storage_state()
    return {'url': _PORTAL, 'storage_state': {
        'cookies': [cookie for cookie in storage['cookies']
                    if cookie.get('domain', '').lstrip('.') in _HOSTS],
        'origins': [origin for origin in storage['origins']
                    if urlsplit(origin.get('origin', '')).hostname in _HOSTS],
    }}
