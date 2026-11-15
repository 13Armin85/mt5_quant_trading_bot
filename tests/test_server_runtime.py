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
    portal.session='existing-session'
    with pytest.raises(AccountError):
        portal.dispatch('/login',{'login':'12345','password':'wrong','account_type':'demo'},None)
    assert not calls and portal.session=='existing-session'
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
