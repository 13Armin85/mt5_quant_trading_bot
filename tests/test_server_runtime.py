import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.account_login import AccountError,AccountLogin
from core.account_portal import AccountPortal
from core.portal_access import account_guard
from core.wine_runtime import WineRuntime


def request(**updates):
    values={'login':'12345','password':'test-only-password','account_type':'demo'}
    values.update(updates)
    return AccountLogin.parse(values)


def test_public_owner_gate_rejects_before_stopping_runtime_or_revoking_session(tmp_path):
    salt=b'not-a-production-salt'
    record={'login':12345,'account_type':'demo','server':'LiteFinance-MT5-Demo','salt':salt.hex(),
        'digest':hashlib.scrypt(b'test-only-password',salt=salt,n=16384,r=8,p=1,dklen=32).hex()}
    path=tmp_path/'access.json'
    path.write_text(json.dumps(record))
    calls=[]
    runtime=SimpleNamespace(login=lambda value:calls.append(value.login) or {'login':value.login})
    portal=AccountPortal('test-token-'*8,runtime,account_guard(path))
    portal.sessions['existing-session']=(__import__('time').monotonic(),runtime)
    with pytest.raises(AccountError):
        portal.dispatch('/login',{'login':'12345','password':'wrong','account_type':'demo'},None)
    assert not calls and 'existing-session' in portal.sessions
    assert portal.dispatch('/login',{'login':'12345','password':'test-only-password','account_type':'demo'},None)['ok']
    assert calls==[12345]


@pytest.mark.parametrize('changes', [
    {'server':'LiteFinance-attacker'},
    {'terminal_path':r'C:\other\terminal64.exe'},
    {'login':'99999'},
])
def test_owner_gate_pins_server_and_rejects_terminal_override(tmp_path, changes):
    salt=b'test-salt'
    record={'login':12345,'account_type':'demo','server':'LiteFinance-MT5-Demo','salt':salt.hex(),
        'digest':hashlib.scrypt(b'test-only-password',salt=salt,n=16384,r=8,p=1,dklen=32).hex()}
    path=tmp_path/'access.json'
    path.write_text(json.dumps(record))
    with pytest.raises(AccountError):
        account_guard(path)(request(**changes))


def test_wine_terminal_resolution_stays_within_prefix(monkeypatch,tmp_path):
    monkeypatch.setenv('WINEPREFIX',str(tmp_path))
    path=tmp_path/'drive_c'/'Program Files'/'MetaTrader 5'/'terminal64.exe'
    path.parent.mkdir(parents=True)
    path.touch()
    runtime=WineRuntime(root=tmp_path)
    assert runtime.resolve_terminal_path(request())==r'C:\Program Files\MetaTrader 5\terminal64.exe'
    with pytest.raises(AccountError):
        runtime.resolve_terminal_path(request(terminal_path=r'Z:\outside\terminal64.exe'))
    with pytest.raises(AccountError):
        runtime.resolve_terminal_path(request(terminal_path=r'C:\..\..\terminal64.exe'))


def test_wine_sdk_receives_password_only_on_stdin(monkeypatch,tmp_path):
    monkeypatch.setenv('WINEPREFIX',str(tmp_path))
    monkeypatch.setenv('NOA_WINE_PYTHON','/windows/python.exe')
    captured=[]
    def run(command,**kwargs):
        captured.append((command,kwargs))
        return SimpleNamespace(stdout=json.dumps({'ok':True,'account':{'login':12345,'account_type':'demo'}}))
    monkeypatch.setattr('core.wine_runtime.subprocess.run',run)
    runtime=WineRuntime(root=tmp_path)
    response=runtime.authenticate_account(request(),r'C:\MT5\terminal64.exe')
    command,options=captured[0]
    assert 'test-only-password' not in repr(command)
    assert json.loads(options['input'])['password']=='test-only-password'
    assert response['account_type']=='demo'


def test_server_bridge_cannot_trade_without_root_provisioned_flag(monkeypatch,tmp_path):
    flag=tmp_path/'demo-trading.enabled'
    monkeypatch.setenv('NOA_DEMO_TRADING_FLAG',str(flag))
    monkeypatch.setenv('NOA_WINE_PYTHON','/windows/python.exe')
    runtime=WineRuntime(root=tmp_path)
    assert not runtime.trading_enabled()
    assert '--allow-live' not in runtime.bridge_command()
    flag.touch()
    assert runtime.trading_enabled()
    assert '--allow-live' in runtime.bridge_command()


def test_cold_snapshot_never_launches_terminal_without_credentials(monkeypatch,tmp_path):
    runtime=WineRuntime(root=tmp_path)
    monkeypatch.setattr(runtime,'bridge_running',lambda:False)
    monkeypatch.setattr(runtime,'terminal_running',lambda:False)
    def forbidden(*a):raise AssertionError('Snapshot must not launch a logged-out terminal')
    monkeypatch.setattr(runtime,'native_probe',forbidden)
    assert runtime.snapshot() is None
    runtime.account={'login':12345}
    with pytest.raises(AccountError):runtime.snapshot()


def test_cold_start_uses_temporary_credentials_with_trading_disabled(monkeypatch,tmp_path):
    runtime=WineRuntime(root=tmp_path)
    monkeypatch.setattr(runtime,'wait_terminal_ready',lambda request:None)
    directory=tmp_path/'ram'
    directory.mkdir()
    monkeypatch.setenv('NOA_MT5_STARTUP_DIR',str(directory))
    monkeypatch.setenv('NOA_WINE_PYTHON','/windows/python.exe')
    monkeypatch.setattr(runtime,'terminal_running',lambda:False)
    calls=[]
    monkeypatch.setattr('core.wine_runtime.subprocess.Popen',lambda command,**kw:calls.append(command))
    with runtime.terminal_startup(request(),r'C:\MT5\terminal64.exe'):
        data=next(directory.glob('*.ini')).read_text(encoding='utf-16')
        assert 'Login=12345' in data and 'Password=test-only-password' in data
        assert 'AllowLiveTrading=0' in data and 'Account=1' in data
        assert 'test-only-password' not in repr(calls)
    assert not list(directory.iterdir())


def test_temporary_startup_credentials_are_removed_after_failure(monkeypatch,tmp_path):
    runtime=WineRuntime(root=tmp_path)
    monkeypatch.setattr(runtime,'wait_terminal_ready',lambda request:None)
    monkeypatch.setenv('NOA_MT5_STARTUP_DIR',str(tmp_path))
    monkeypatch.setenv('NOA_WINE_PYTHON','/windows/python.exe')
    monkeypatch.setattr(runtime,'terminal_running',lambda:False)
    monkeypatch.setattr('core.wine_runtime.subprocess.Popen',lambda *a,**kw:None)
    with pytest.raises(RuntimeError):
        with runtime.terminal_startup(request(),r'C:\MT5\terminal64.exe'):
            raise RuntimeError('Authentication failed')
    assert not list(tmp_path.glob('owner-*.ini'))


def test_demo_session_authorization_follows_actual_sdk_identity_verification(monkeypatch,tmp_path):
    runtime=WineRuntime(root=tmp_path)
    monkeypatch.setattr(runtime,'trading_enabled',lambda:True)
    events=[]
    def probe(action,payload):
        events.append(('authenticate',payload['_trading_authorized']))
        return {'login':12345,'server':'LiteFinance-MT5-Demo','account_type':'demo',
            'trade_allowed':payload['_trading_authorized']}
    monkeypatch.setattr(runtime,'native_probe',probe)
    monkeypatch.setattr(runtime,'authorize_demo_session',lambda r,a:events.append(('verified-demo',a['login'])))
    assert runtime.authenticate_account(request(),r'C:\MT5\terminal64.exe')['trade_allowed']
    assert events==[('authenticate',False),('verified-demo',12345),('authenticate',True)]


@pytest.mark.parametrize('updates',[{'login':99999},{'account_type':'real'},{'server':'LiteFinance-MT5-Live'}])
def test_session_permission_change_rejects_any_non_owner_or_non_demo_account(monkeypatch,tmp_path,updates):
    runtime=WineRuntime(root=tmp_path)
    monkeypatch.setattr(runtime,'trading_enabled',lambda:True)
    account={'login':12345,'server':'LiteFinance-MT5-Demo','account_type':'demo',
        'account_trade_allowed':True,'expert_allowed':True,'python_api_disabled':False}
    account.update(updates)
    def forbidden(*a,**kw):raise AssertionError('No UI event is permitted for another account')
    monkeypatch.setattr('core.wine_runtime.subprocess.run',forbidden)
    with pytest.raises(AccountError):runtime.authorize_demo_session(request(),account)
