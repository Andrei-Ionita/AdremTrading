"""Encrypted, account-bound FusionSolar browser sessions."""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


PORTAL_ROUTING_KEYS = frozenset(("app-id", "instance-id", "zone-id"))


def is_portal_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
        routing = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True) if parsed.query else []
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").endswith(".fusionsolar.huawei.com")
        and port in (None, 443)
        and not parsed.username and not parsed.password
        and len({key for key, _ in routing}) == len(routing)
        and all(key in PORTAL_ROUTING_KEYS and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value)
                for key, value in routing)
        and parsed.path.startswith("/uniportal/pvmswebsite/")
        and parsed.fragment.startswith(("/home/", "/view/station/"))
    )


def session_portal_url(url: str) -> str:
    """Keep application routing, but never persist transient login query tokens."""
    parsed = urlsplit(url)
    query = urlencode([(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                       if key in PORTAL_ROUTING_KEYS])
    result = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))
    if not is_portal_url(result):
        raise ValueError("Invalid FusionSolar session destination.")
    return result


def restore_portal_url(saved_url: str, configured_url: str, region_name: str | None) -> str:
    """Repair older snapshots that discarded FusionSolar's application routing."""
    saved = urlsplit(session_portal_url(saved_url))
    routing = dict(parse_qsl(saved.query))
    configured = urlsplit(configured_url)
    if (configured.scheme, configured.netloc, configured.path) == (saved.scheme, saved.netloc, saved.path):
        for key, value in parse_qsl(configured.query):
            if key in PORTAL_ROUTING_KEYS:
                routing.setdefault(key, value)
    if (saved.path == "/uniportal/pvmswebsite/assets/build/cloud.html"
            and re.fullmatch(r"region\d{3}", region_name or "", re.I)):
        # Legacy Horeco snapshots have only a regional host; its configured
        # entry is SSO, so recover the SmartPVMS route using its explicit region.
        for key, value in (("app-id", "smartpvms"), ("instance-id", "smartpvms"),
                           ("zone-id", region_name.lower())):
            routing.setdefault(key, value)
    return session_portal_url(urlunsplit((saved.scheme, saved.netloc, saved.path,
                                         urlencode(routing), saved.fragment)))


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
        sessions = state.get("session_storage", [])
        if not isinstance(sessions, list):
            raise ValueError("Invalid FusionSolar tab state.")
        for origin in storage["origins"] + sessions:
            parsed = urlsplit(origin["origin"])
            if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".fusionsolar.huawei.com"):
                raise ValueError("Unexpected origin in FusionSolar session.")
        for session in sessions:
            if not isinstance(session.get("items"), list) or any(
                not isinstance(item.get("name"), str) or not isinstance(item.get("value"), str)
                for item in session["items"]
            ):
                raise ValueError("Invalid FusionSolar tab storage values.")

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
