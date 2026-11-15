import json
from types import SimpleNamespace

import pytest

from core.account_portal import DesktopRuntime
from core.account_login import AccountError
from scripts import server_docker


@pytest.mark.parametrize('args', [
    ['run','--privileged','trader','sh'],
    ['up','-d','--no-build','dashboard'],
    ['-f','/tmp/attacker.yaml','up'],
    ['exec','trader','sh'],
    ['up','-d','--no-build','trader','--force-recreate'],
    {'operation':'up'}, [123],
])
def test_controller_rejects_arbitrary_docker_operations(monkeypatch,args):
    def forbidden(*a,**kw):
        raise AssertionError('Untrusted operation reached Docker')
    monkeypatch.setattr(server_docker.subprocess,'run',forbidden)
    with pytest.raises(ValueError):
        server_docker.execute(args)


def test_controller_uses_only_owner_demo_identity_and_clean_environment(tmp_path,monkeypatch):
    access=tmp_path/'access.json'
    access.write_text(json.dumps({'login':12345,'server':'LiteFinance-MT5-Demo','account_type':'demo'}))
    monkeypatch.setattr(server_docker,'ACCESS',access)
    monkeypatch.setattr(server_docker,'APP',tmp_path)
    monkeypatch.setattr(server_docker,'TRADING_FLAG',tmp_path/'demo-trading.enabled')
    monkeypatch.setattr(server_docker,'service_gid',lambda:987)
    monkeypatch.setenv('MT5_PASSWORD','must-not-propagate')
    monkeypatch.setenv('DOCKER_HOST','tcp://attacker.example:2375')
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs))
        return SimpleNamespace(returncode=0,stdout='trader\n',stderr='')
    monkeypatch.setattr(server_docker.subprocess,'run',run)
    assert server_docker.execute(['up','-d','--no-build','trader'])['returncode']==0
    command,options=calls[0]
    assert command[:5]==['/usr/bin/docker','compose','--env-file','/dev/null','-f']
    assert options['env']['MT5_LOGIN']=='12345'
    assert options['env']['NOA_SERVICE_GID']=='987'
    assert 'DOCKER_HOST' not in options['env'] and 'MT5_PASSWORD' not in options['env']
    assert options['env']['ENABLE_LIVE_TRADING']=='false'
    assert options['env']['NOA_RUN_MODE']=='paper'
    server_docker.TRADING_FLAG.touch()
    server_docker.execute(['up','-d','--no-build','trader'])
    assert calls[-1][1]['env']['ENABLE_LIVE_TRADING']=='true'
    access.write_text(json.dumps({'login':12345,'server':'LiteFinance-MT5-Live','account_type':'real'}))
    with pytest.raises(ValueError):
        server_docker.execute(['start','trader'])
    assert len(calls)==2


def test_portal_server_control_fails_closed_without_docker_fallback(monkeypatch,tmp_path):
    monkeypatch.setenv('NOA_DOCKER_CONTROL_SOCKET','/run/test-control.sock')
    def fail(*args):
        raise OSError('Unavailable')
    monkeypatch.setattr('core.server_control.compose_request',fail)
    def forbidden(*a,**kw):
        raise AssertionError('Must never fall back to direct Docker')
    monkeypatch.setattr('core.account_portal.subprocess.run',forbidden)
    with pytest.raises(AccountError):
        DesktopRuntime(root=tmp_path).compose('stop','trader')
