import json
import sqlite3
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pytest

from config import EnvSettings
from core.bridge_protocol import encode, decode
from core.mt5_bridge import BridgeService
from core.mt5_bridge import BridgeServer, handler_for
from core.mt5_client import MT5Error
from core.mt5_remote import RemoteMT5Client


def call(service, owner, method, *args, request_id=None, **kwargs):
    return service.dispatch({"owner": owner, "method": method, "id": request_id or str(uuid.uuid4()),
                             "args": encode(args), "kwargs": encode(kwargs)})


@pytest.fixture
def service(broker, tmp_path):
    broker.market_timezone = ZoneInfo("Europe/Helsinki")
    broker.account.login, broker.account.server = 123, "Test"
    broker.initialize = lambda: True
    bridge = BridgeService(broker, "test-token-" * 8, tmp_path / "bridge.db", allow_live=True)
    yield bridge
    bridge.db.close()


def connect(service, owner=None, trading=True):
    owner = owner or str(uuid.uuid4())
    call(service, owner, "connect", trading=trading, market_timezone="Europe/Helsinki")
    return owner


def order():
    return {"action": 1, "symbol": "EURUSD", "type": 0, "volume": .01, "price": 1.1,
            "sl": 1.09, "tp": 1.12, "magic": 260912, "comment": "test"}


def test_typed_wire_round_trip_preserves_structs_arrays_dates():
    bars = np.array([(1792774800, 1.2, 99)], dtype=[("time", "i8"), ("close", "f8"), ("tick_volume", "u8")])
    data = {"bars": bars, "when": datetime.now(timezone.utc), "tick": SimpleNamespace(time=1792774800, bid=1.2)}
    restored = decode(json.loads(json.dumps(encode(data), allow_nan=False)))
    assert np.array_equal(restored["bars"], bars)
    assert restored["bars"].dtype == bars.dtype
    assert restored["when"] == data["when"]
    assert restored["tick"].bid == 1.2
    with pytest.raises(ValueError):
        decode({"$array": [["unsafe"]], "dtype": [["payload", "O"]]})
    with pytest.raises(ValueError):
        encode(datetime.now())


def test_authentication_allowlist_and_read_only_session(service, broker):
    assert service.authorized("Bearer " + service.token)
    assert not service.authorized(None) and not service.authorized("Bearer wrong")
    owner = connect(service, trading=False)
    with pytest.raises(ValueError, match="lease"):
        call(service, owner, "order_send", order())
    for method in ("shutdown", "initialize", "__class__", "__getattribute__"):
        with pytest.raises(ValueError, match="permitted"):
            call(service, owner, method)
    assert not broker.sent


def test_exclusive_writer_lease_survives_read_only_clients(service):
    owner = connect(service)
    reader = connect(service, trading=False)
    call(service, reader, "disconnect")
    with pytest.raises(ValueError, match="Another LIVE"):
        connect(service)
    call(service, owner, "disconnect")
    assert connect(service) != owner


def test_expired_writer_cannot_trade_after_takeover(service):
    old = connect(service)
    service.lease_until = 0
    connect(service)
    with pytest.raises(ValueError, match="lease"):
        call(service, old, "order_send", order())


def test_bridge_live_gate_and_timezone_must_match(service):
    service.allow_live = False
    with pytest.raises(ValueError, match="disabled"):
        connect(service)
    with pytest.raises(ValueError, match="zones differ"):
        call(service, str(uuid.uuid4()), "connect", trading=False, market_timezone="UTC")


def test_order_deduplication_persists_across_bridge_restart(service, broker, tmp_path):
    owner, request_id = connect(service), str(uuid.uuid4())
    first = call(service, owner, "order_send", order(), request_id=request_id)
    service.db.close()
    service.db = sqlite3.connect(tmp_path / "bridge.db")
    second = call(service, owner, "order_send", order(), request_id=request_id)
    assert first == second and len(broker.sent) == 1
    changed = dict(order(), volume=.02)
    with pytest.raises(ValueError, match="reused"):
        call(service, owner, "order_send", changed, request_id=request_id)


def test_ambiguous_order_is_never_resubmitted(service, broker):
    owner, request_id = connect(service), str(uuid.uuid4())
    def uncertain(req):
        broker.sent.append(req)
        raise TimeoutError("Broker may already have filled")
    broker.order_send = uncertain
    assert call(service, owner, "order_send", order(), request_id=request_id) is None
    assert call(service, owner, "order_send", order(), request_id=request_id) is None
    assert len(broker.sent) == 1


def test_bridge_blocks_unprotected_entries_and_foreign_position_changes(service, broker):
    owner = connect(service)
    for bad in (dict(order(), sl=0), dict(order(), tp=1.0), dict(order(), magic=1), dict(order(), action=5)):
        with pytest.raises(ValueError):
            call(service, owner, "order_send", bad)
    broker.positions = [SimpleNamespace(ticket=7, magic=10, symbol="EURUSD", type=0, volume=.01, sl=1.09)]
    with pytest.raises(ValueError, match="belong"):
        call(service, owner, "order_send", dict(order(), position=7, type=1))
    broker.positions[0].magic = 260912
    with pytest.raises(ValueError, match="widen"):
        call(service, owner, "order_send", dict(order(), position=7, action=6, sl=1.08))
    assert not broker.sent


def make_client(tmp_path, transport, trading=False):
    token = tmp_path / "token"
    token.write_text("test-token-" * 8)
    env = EnvSettings(_env_file=None, MT5_BRIDGE_URL="http://bridge.test", MT5_BRIDGE_TOKEN_FILE=str(token),
                      MT5_LOGIN=123, MT5_SERVER="Test")
    return RemoteMT5Client(env, "Europe/Helsinki", trading, transport)


def test_remote_client_calls_normalized_bridge_without_local_sdk(service, broker, tmp_path, monkeypatch):
    monkeypatch.setattr("core.mt5_client.mt5", None)
    normalized = 1792774800
    broker.symbol_info_tick = lambda symbol: SimpleNamespace(time=normalized, time_msc=normalized * 1000, bid=1.1, ask=1.1002)
    def transport(request):
        assert service.authorized(request.headers["authorization"])
        return httpx.Response(200, json={"ok": True, "result": service.dispatch(json.loads(request.content))})
    client = make_client(tmp_path, httpx.MockTransport(transport))
    try:
        assert client.initialize() and client.health_check()
        assert client.symbol_info_tick("EURUSD").time == normalized  # No second timezone conversion.
        with pytest.raises(MT5Error, match="Read-only"):
            client.order_send(order())
    finally:
        client.shutdown()
    assert not broker.sent


def test_remote_order_timeout_is_one_shot(tmp_path):
    calls = []
    def transport(request):
        calls.append(request)
        raise httpx.ReadTimeout("unknown", request=request)
    client = make_client(tmp_path, httpx.MockTransport(transport), trading=True)
    with pytest.raises(MT5Error, match="unknown"):
        client.order_send(order())
    assert len(calls) == 1
    client.shutdown()


def test_http_bridge_end_to_end_with_simulated_broker(service, broker, tmp_path):
    from threading import Thread
    # Production handles all SDK/SQLite operations on its one HTTP server thread.
    # This test constructs the fixture outside that thread, so permit its transfer.
    service.db.close()
    service.db = sqlite3.connect(tmp_path / "bridge.db", check_same_thread=False)
    server = BridgeServer(("127.0.0.1", 0), handler_for(service))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = make_client(tmp_path, None, trading=True)
    client.http.base_url = f"http://127.0.0.1:{server.server_port}/"
    try:
        with httpx.Client(trust_env=False) as http:
            assert http.post(str(client.http.base_url) + "rpc", json={}).status_code == 401
            assert http.post(str(client.http.base_url) + "rpc", json={}, headers={
                "Authorization": "Bearer " + service.token, "Origin": "http://untrusted.test"}).status_code == 403
        assert client.initialize() and client.health_check()
        assert client.symbol_info_tick("EURUSD").ask == broker.quote.ask
        result = client.order_send(order())
        assert result.retcode == 10009 and len(broker.sent) == 1
    finally:
        client.shutdown()
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_bridge_shutdown_requires_released_live_lease(service):
    owner = connect(service)
    admin = str(uuid.uuid4())
    with pytest.raises(ValueError, match="Stop the Docker"):
        call(service, admin, "stop_bridge")
    call(service, owner, "disconnect")
    assert call(service, admin, "stop_bridge") is True
