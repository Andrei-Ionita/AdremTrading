import base64
import json
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from power_reading.scrapers.fusionsolar_scraper import PowerSnapshot


DEFAULT_PCSUN_TAG = '"UMG_SCALE"."UMG512"."Sum; Psum3=P1+P2+P3"'


class PCSunScraper:
    def __init__(
        self,
        target_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        http_username: Optional[str] = None,
        http_password: Optional[str] = None,
        active_power_tag: Optional[str] = None,
        timeout_sec: int = 60,
        source_name: str = "pcsun",
    ) -> None:
        self.target_url = target_url
        self.username = username
        self.password = password
        self.http_username = http_username
        self.http_password = http_password
        self.active_power_tag = (active_power_tag or DEFAULT_PCSUN_TAG).strip()
        self.timeout_sec = max(5, int(timeout_sec))
        self.source_name = source_name.strip() or "pcsun"

    def scrape_once(self) -> PowerSnapshot:
        token = self._login()
        value = self._read_value(token, self.active_power_tag)
        self._logout(token)
        pv_kw = float(value) if value is not None else None
        return PowerSnapshot(
            pv_kw=pv_kw,
            load_kw=None,
            grid_kw=None,
            timestamp_utc=datetime.now(tz=timezone.utc).isoformat(),
            source=f"{self.source_name}-jsonrpc",
            raw_excerpt=f"tag={self.active_power_tag} value={pv_kw}",
        )

    def _api_url(self) -> str:
        parts = urlsplit(self.target_url)
        if not parts.scheme or not parts.netloc:
            raise RuntimeError(f"Invalid PCSun URL: {self.target_url}")
        return f"{parts.scheme}://{parts.netloc}/api/jsonrpc"

    def _headers(self, token: Optional[str] = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.http_username and self.http_password:
            basic = base64.b64encode(f"{self.http_username}:{self.http_password}".encode()).decode()
            headers["Authorization"] = f"Basic {basic}"
        if token:
            headers["X-Auth-Token"] = token
        return headers

    def _rpc(self, method: str, params: Optional[dict] = None, rpc_id: int = 1, token: Optional[str] = None):
        payload = {"jsonrpc": "2.0", "method": method, "id": rpc_id}
        if params:
            payload["params"] = params
        req = Request(
            self._api_url(),
            data=json.dumps(payload).encode(),
            headers=self._headers(token=token),
        )
        raw = urlopen(req, timeout=self.timeout_sec).read().decode()
        body = json.loads(raw)
        if body.get("error"):
            raise RuntimeError(f"PCSun RPC {method} failed: {body['error']}")
        return body.get("result")

    def _login(self) -> str:
        if not (self.username and self.password):
            raise RuntimeError("PCSun app credentials are missing.")
        result = self._rpc(
            "Api.Login",
            params={"user": self.username, "password": self.password},
            rpc_id=1,
        )
        token = (result or {}).get("token")
        if not token:
            raise RuntimeError("PCSun login did not return a token.")
        return str(token)

    def _read_value(self, token: str, tag_name: str):
        return self._rpc(
            "PlcProgram.Read",
            params={"var": tag_name},
            rpc_id=2,
            token=token,
        )

    def _logout(self, token: str) -> None:
        try:
            self._rpc("Api.Logout", rpc_id=3, token=token)
        except Exception:
            pass

