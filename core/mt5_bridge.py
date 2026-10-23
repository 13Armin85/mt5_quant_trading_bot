"""Authenticated, allowlisted Windows MT5 adapter for Docker Desktop.

Run in the logged-in Windows user's session, never in a Linux container.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import sqlite3
import time
import uuid
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from loguru import logger

from config import ROOT, load_config
from core.bridge_protocol import READ_METHODS, encode, decode
from core.mt5_client import MT5Client, MT5Error
from core.runtime_lock import RuntimeLock


class BridgeService:
    LEASE_SECONDS = 300

    def __init__(self, client, token, journal_path, allow_live=False, magic=260912):
        if len(token) < 32:
            raise ValueError("Bridge token must contain at least 32 characters")
        self.client, self.token = client, token
        self.allow_live, self.magic = allow_live, int(magic)
        self.writer = None
        self.lease_until = 0
        self.sessions = {}
        self.stop_requested = False
        self.db = sqlite3.connect(journal_path)
        self.db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT)")
        self.db.commit()

    def authorized(self, header):
        return hmac.compare_digest(header or "", "Bearer " + self.token)

    def dispatch(self, payload):
        method, owner = payload["method"], payload["owner"]
        uuid.UUID(owner)
        args, kwargs = decode(payload.get("args", [])), decode(payload.get("kwargs", {}))
        now = time.monotonic()
        self.sessions = {k: v for k, v in self.sessions.items() if v > now}
        if method == "connect":
            if args or set(kwargs) != {"trading", "market_timezone"} or type(kwargs["trading"]) is not bool:
                raise ValueError("Invalid connect request")
            if kwargs["market_timezone"] != self.client.market_timezone.key:
                raise ValueError("Bridge and engine market time zones differ")
            if kwargs["trading"]:
                if not self.allow_live:
                    raise ValueError("LIVE is disabled on the Windows bridge")
                if self.writer not in (None, owner) and now < self.lease_until:
                    raise ValueError("Another LIVE engine owns the bridge lease")
            if not self.client.health_check():
                self.client.initialize()
            if kwargs["trading"]:
                self.writer, self.lease_until = owner, now + self.LEASE_SECONDS
            self.sessions[owner] = now + self.LEASE_SECONDS
            # Account credentials/passwords never leave Windows.
            account = self.client.account_info()
            return {"$record": {"login": int(account.login), "server": str(account.server), "bridge_live": self.allow_live}}
        if method == "stop_bridge":
            if self.writer is not None and now < self.lease_until:
                raise ValueError("Stop the Docker trader before stopping its bridge")
            self.stop_requested = True
            return True
        if method == "disconnect":
            self.sessions.pop(owner, None)
            if self.writer == owner:
                self.writer = None
            return None  # A read-only client cannot shut down the shared terminal.
        if owner not in self.sessions:
            raise ValueError("Bridge session expired; reconnect required")
        self.sessions[owner] = now + self.LEASE_SECONDS
        if self.writer == owner and now < self.lease_until:
            self.lease_until = now + self.LEASE_SECONDS
        if method == "health_check":
            return bool(self.client.health_check())
        if method not in READ_METHODS and method != "order_send":
            raise ValueError("Method is not permitted")
        if method == "order_send":
            if not self.allow_live or self.writer != owner or now >= self.lease_until:
                raise ValueError("An active exclusive LIVE lease is required")
            if not self.client.health_check():
                raise MT5Error("MT5 account/terminal is unavailable")
            request_id = str(uuid.UUID(payload["id"]))
            digest = hashlib.sha256(json.dumps({"args": payload.get("args"), "kwargs": payload.get("kwargs")}, sort_keys=True).encode()).hexdigest()
            previous = self.db.execute("SELECT digest, result FROM requests WHERE id=?", (request_id,)).fetchone()
            if previous:
                if previous[0] != digest:
                    raise ValueError("A request ID cannot be reused for another order")
                # A crash after SUBMITTING leaves NULL: return UNKNOWN; never resend.
                return json.loads(previous[1]) if previous[1] is not None else None
            if len(args) != 1 or kwargs or not isinstance(args[0], dict):
                raise ValueError("Invalid order request")
            self.validate_order(args[0])
            self.db.execute("INSERT INTO requests(id, digest) VALUES (?, ?)", (request_id, digest))
            self.db.commit()
            try:
                result = encode(self.client.order_send(args[0]))
            except Exception:
                logger.warning("Order result unknown; inspect broker history before any further action")
                return None
            self.db.execute("UPDATE requests SET result=? WHERE id=?", (json.dumps(result, allow_nan=False), request_id))
            self.db.commit()
            return result
        return encode(getattr(self.client, method)(*args, **kwargs))

    def validate_order(self, request):
        if int(request.get("magic", -1)) != self.magic:
            raise ValueError("Order magic differs from configured bot")
        action = request.get("action")
        if action not in (1, 6):
            raise ValueError("Only market deals and position SL/TP updates are supported")
        for field in ("volume", "price", "sl", "tp"):
            if field in request and not math.isfinite(float(request[field])):
                raise ValueError("Order values must be finite")
        if request.get("position"):
            positions = self.client.positions_get(ticket=int(request["position"]))
            position = next((p for p in positions or () if int(p.ticket) == int(request["position"])), None)
            if position is None or int(position.magic) != self.magic or position.symbol != request.get("symbol"):
                raise ValueError("Position does not belong to this bot")
            if action == 1:
                if request.get("type") != (1 if int(position.type) == 0 else 0) or not 0 < float(request.get("volume", 0)) <= float(position.volume):
                    raise ValueError("Close must reduce the bot position")
            else:
                sl, tp = float(request.get("sl", 0)), float(request.get("tp", 0))
                if sl <= 0 or tp <= 0 or (position.sl and (sl < position.sl if int(position.type) == 0 else sl > position.sl)):
                    raise ValueError("SL/TP update cannot remove protection or widen the stop")
        else:
            if action != 1 or request.get("type") not in (0, 1):
                raise ValueError("Only BUY/SELL entries are supported")
            price, sl, tp = (float(request.get(k, 0)) for k in ("price", "sl", "tp"))
            if not (0 < sl < price < tp if request["type"] == 0 else 0 < tp < price < sl):
                raise ValueError("Entry must contain correctly sided SL and TP")
            if float(request.get("volume", 0)) <= 0:
                raise ValueError("Entry volume must be positive")


def handler_for(service):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log authorization or broker request/response bodies.

        def do_POST(self):
            if not service.authorized(self.headers.get("Authorization")):
                self.send_error(401)
                return
            if self.path != "/rpc" or self.headers.get("Origin"):
                self.send_error(403)
                return
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 4_000_000:
                    raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(size))
                result = {"ok": True, "result": service.dispatch(payload)}
            except (ValueError, KeyError, TypeError, AttributeError, MT5Error) as exc:
                # Only our fixed validation messages are sent, never SDK exceptions.
                result = {"ok": False, "error": str(exc) if isinstance(exc, (ValueError, MT5Error)) else "Invalid request"}
            except Exception:
                logger.error("Bridge operation failed; client must reconcile uncertain orders")
                result = {"ok": False, "error": "Bridge unavailable; order outcome may be unknown"}
            body = json.dumps(result, allow_nan=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if service.stop_requested:
                Thread(target=self.server.shutdown, daemon=True).start()
    return Handler


class BridgeServer(HTTPServer):
    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(15)
        return sock, address


def main():
    parser = argparse.ArgumentParser(description="Windows MT5 bridge for Docker Desktop")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    cfg = load_config(); cfg.validate_runtime()
    if args.allow_live and not cfg.env.enable_live_trading:
        raise ValueError("Set ENABLE_LIVE_TRADING=true explicitly before starting a LIVE bridge")
    token = Path(cfg.env.mt5_bridge_token_file).read_text().strip()
    journal = ROOT / ".docker" / "bridge_requests.db"
    journal.parent.mkdir(exist_ok=True)
    client = MT5Client(cfg.env, cfg.get("trading.mt5_time_zone", "UTC"))
    # The native bot uses the same lock: local and Docker LIVE cannot run together.
    with RuntimeLock(cfg.env.runtime_lock_path):
        service = BridgeService(client, token, journal, args.allow_live, cfg.get("trading.magic", 260912))
        try:
            with BridgeServer((args.bind, args.port), handler_for(service)) as server:
                logger.info("Windows MT5 bridge ready; LIVE={}", args.allow_live)
                server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            client.shutdown()
            service.db.close()


if __name__ == "__main__":
    main()
