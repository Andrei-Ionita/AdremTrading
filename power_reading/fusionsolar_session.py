"""Encrypted, account-bound FusionSolar browser sessions."""
from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


def is_portal_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").endswith(".fusionsolar.huawei.com")
        and parsed.port in (None, 443)
        and not parsed.username and not parsed.password and not parsed.query
        and parsed.path.startswith("/uniportal/pvmswebsite/")
        and parsed.fragment.startswith(("/home/", "/view/station/"))
    )


class FusionSolarSessionStore:
    def __init__(self, asset: str, username: str, password: str, profile_dir: Path):
        self.asset = asset
        self.username = username
        self.password = password
        self.path = Path(profile_dir) / "fusion-session.enc"

    def _cipher(self, salt: bytes) -> Fernet:
        secret = json.dumps([self.asset, self.username, self.password]).encode()
        key = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(secret)
        return Fernet(base64.urlsafe_b64encode(key))

    def encode(self, state: dict) -> str:
        if not self.username or not self.password:
            raise ValueError("FusionSolar credentials are required to protect the session.")
        self._validate(state)
        salt = os.urandom(16)
        token = self._cipher(salt).encrypt(json.dumps(state).encode())
        return base64.urlsafe_b64encode(salt + token).decode()

    def decode(self, value: str) -> dict | None:
        try:
            packed = base64.urlsafe_b64decode(value)
            state = json.loads(self._cipher(packed[:16]).decrypt(packed[16:]))
            self._validate(state)
            return state
        except (ValueError, TypeError, KeyError, InvalidToken):
            # Rotation, corrupt state, and another account's session require login.
            return None

    @staticmethod
    def _validate(state: dict) -> None:
        if not isinstance(state, dict) or not is_portal_url(state.get("url", "")):
            raise ValueError("Invalid FusionSolar session destination.")
        storage = state["storage_state"]
        if not isinstance(storage.get("cookies"), list) or not isinstance(storage.get("origins"), list):
            raise ValueError("Invalid FusionSolar browser state.")
        for cookie in storage["cookies"]:
            domain = cookie["domain"].lstrip(".")
            if domain != "fusionsolar.huawei.com" and not domain.endswith(".fusionsolar.huawei.com"):
                raise ValueError("Unexpected cookie domain in FusionSolar session.")
        for origin in storage["origins"]:
            parsed = urlsplit(origin["origin"])
            if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".fusionsolar.huawei.com"):
                raise ValueError("Unexpected origin in FusionSolar session.")

    def _uses_database(self) -> bool:
        from .database import get_database_url
        try:
            get_database_url()
            return True
        except RuntimeError:
            return False

    def load(self) -> dict | None:
        if self._uses_database():
            from .database import _connection_scope
            with _connection_scope() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT encrypted_state FROM fusion_solar_sessions WHERE asset=%s", (self.asset,))
                    row = cursor.fetchone()
            return self.decode(row[0]) if row else None
        try:
            return self.decode(self.path.read_text(encoding="ascii"))
        except FileNotFoundError:
            return None

    def save(self, state: dict) -> None:
        value = self.encode(state)
        if self._uses_database():
            from .database import _connection_scope
            with _connection_scope() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""INSERT INTO fusion_solar_sessions (asset, encrypted_state, updated_at)
                        VALUES (%s, %s, NOW()) ON CONFLICT (asset) DO UPDATE SET
                        encrypted_state=EXCLUDED.encrypted_state, updated_at=EXCLUDED.updated_at""", (self.asset, value))
                conn.commit()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", dir=self.path.parent,
                                             prefix=".fusion-session-", delete=False) as file:
                temporary = Path(file.name)
                file.write(value)
            os.replace(temporary, self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
