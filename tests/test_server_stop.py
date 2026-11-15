from types import SimpleNamespace

import pytest

from scripts import server_stop


def test_shutdown_releases_engine_before_bridge_without_revoking_auto_login(monkeypatch,tmp_path):
    flag=tmp_path/'auto_login'
    flag.touch()
    monkeypatch.setenv('NOA_AUTO_LOGIN_FLAG',str(flag))
    monkeypatch.setenv('NOA_DOCKER_CONTROL_SOCKET','test-socket')
    events=[]
    monkeypatch.setattr(server_stop,'compose_request',lambda *a:events.append('trader') or {'returncode':0})
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*a):pass
    monkeypatch.setattr(server_stop.socket,'create_connection',lambda *a,**k:Connection())
    class Client:
        def __init__(self,*args):self.http=SimpleNamespace(close=lambda:events.append('closed'))
        def _rpc(self,method):events.append(method)
    monkeypatch.setattr(server_stop,'RemoteMT5Client',Client)
    server_stop.main()
    assert events==['trader','stop_bridge','closed']
    assert flag.exists()


def test_bridge_is_not_stopped_if_trader_shutdown_fails(monkeypatch):
    monkeypatch.setenv('NOA_DOCKER_CONTROL_SOCKET','test-socket')
    monkeypatch.setattr(server_stop,'compose_request',lambda *a:{'returncode':1})
    def forbidden(*a,**k):raise AssertionError('Engine still owns the bridge')
    monkeypatch.setattr(server_stop.socket,'create_connection',forbidden)
    with pytest.raises(RuntimeError):server_stop.main()
