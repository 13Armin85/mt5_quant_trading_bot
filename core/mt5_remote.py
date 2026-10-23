"""Client for the native Windows bridge. Order submissions are never retried."""
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from core.bridge_protocol import READ_METHODS, encode, decode
from core.mt5_client import MT5Client, MT5Error


class RemoteMT5Client:
    def __init__(self, env, market_timezone="UTC", trading=False, transport=None):
        self.env = env
        self.market_timezone = ZoneInfo(market_timezone)
        self.trading = trading
        self.owner = str(uuid.uuid4())
        self.connected = False
        self.account_identity = None
        self.http = httpx.Client(
            base_url=env.mt5_bridge_url.rstrip("/"), timeout=90, trust_env=False,
            headers={"Authorization": "Bearer " + Path(env.mt5_bridge_token_file).read_text().strip()},
            transport=transport,
        )

    def _rpc(self, method, *args, **kwargs):
        try:
            # No HTTP transport retries, including order_send on timeouts/disconnects.
            response = self.http.post("/rpc", json={"method": method, "owner": self.owner,
                "id": str(uuid.uuid4()), "args": encode(args), "kwargs": encode(kwargs)})
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise MT5Error("MT5 bridge rejected request: " + payload.get("error", "unavailable"))
            return decode(payload["result"])
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # Do not log URLs, headers, credentials or raw response bodies.
            raise MT5Error("MT5 bridge request failed; an order result may be unknown") from None

    def initialize(self):
        info = self._rpc("connect", trading=self.trading, market_timezone=self.market_timezone.key)
        identity = (int(info.login), str(info.server))
        if ((self.env.mt5_login and identity[0] != self.env.mt5_login)
                or (self.env.mt5_server and identity[1] != self.env.mt5_server)
                or (self.account_identity is not None and identity != self.account_identity)):
            self._rpc("disconnect")
            raise MT5Error("Bridge account differs from the intended account")
        self.account_identity = identity
        self.bridge_live = bool(getattr(info, "bridge_live", False))
        self.connected = True
        return True

    def health_check(self):
        if not self.connected:
            return False
        try:
            return bool(self._rpc("health_check"))
        except MT5Error:
            return False

    def reconnect(self, attempts=3, delay=5):
        self.connected = False
        for attempt in range(attempts):
            try:
                return self.initialize()
            except MT5Error:
                if attempt + 1 < attempts:
                    time.sleep(delay)
        raise MT5Error("MT5 bridge reconnect attempts exhausted")

    def shutdown(self):
        try:
            if self.connected:
                self._rpc("disconnect")
        except MT5Error:
            pass
        finally:
            self.connected = False
            self.http.close()

    def order_send(self, request):
        if not self.trading:
            raise MT5Error("Read-only bridge client cannot submit orders")
        return self._rpc("order_send", request)

    def __getattr__(self, method):
        if method in READ_METHODS:
            return lambda *args, **kwargs: self._rpc(method, *args, **kwargs)
        raise AttributeError(method)


def create_mt5_client(cfg, trading=False):
    timezone = cfg.get("trading.mt5_time_zone", "UTC")
    if cfg.env.mt5_bridge_url:
        return RemoteMT5Client(cfg.env, timezone, trading)
    return MT5Client(cfg.env, timezone)
