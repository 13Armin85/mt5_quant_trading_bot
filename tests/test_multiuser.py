import json
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from config import load_config
from core.account_login import AccountError, AccountLogin
from core.account_portal import AccountPortal, DesktopRuntime, handler_for
from core.tenancy import TenantManager, tenant_key, checked_key
from dashboard.account_client import AccountPortalClient
from scripts.tenant_gateway import GatewayServer
from scripts import tenant_controller as controller


def credentials(login=111):
    return {'login':str(login),'password':f'correct-{login}','account_type':'demo','server':'LiteFinance-MT5-Demo'}


class Runtime:
    def __init__(self, login):
        self.account={'login':login,'server':'LiteFinance-MT5-Demo','account_type':'demo'}
        self.running=True
        self.stops=0

    def login(self, request):
        if request.password!=f"correct-{self.account['login']}":
            raise AccountError('Invalid credentials')
        return self.account

    def status(self):return {'connected':True,'account':self.account,'trading_requested':self.running,'view_api':True}
    def view(self):return {'private_account':self.account['login']}
    def stop_trading(self):self.stops+=1;self.running=False
    def start_trading(self):self.running=True


@pytest.fixture
def multiuser(tmp_path):
    runtimes={111:Runtime(111),222:Runtime(222)}
    manager=SimpleNamespace(for_account=lambda request:runtimes[request.login])
    portal=AccountPortal('gateway-token-'*8,manager)
    server=GatewayServer(('127.0.0.1',0),handler_for(portal))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    token=tmp_path/'gateway.token';token.write_text(portal.token)
    client=AccountPortalClient(f'http://127.0.0.1:{server.server_port}',str(token))
    yield client,portal,runtimes
    server.shutdown();server.server_close();thread.join(timeout=5)


def test_simultaneous_users_cannot_read_or_stop_each_others_account(multiuser):
    client,portal,runtimes=multiuser
    a=client.request('/login',payload=credentials(111))['session']
    b=client.request('/login',payload=credentials(222))['session']
    assert client.request('/view',session=a)['view']=={'private_account':111}
    assert client.request('/view',session=b)['view']=={'private_account':222}
    # A forged account selector is ignored: authority comes only from session.
    assert client.request('/stop',payload={'tenant':tenant_key(222,'LiteFinance-MT5-Demo')},session=a)['ok']
    assert not runtimes[111].running and runtimes[222].running
    assert runtimes[111].stops==1 and runtimes[222].stops==0
    assert client.request('/start',payload={},session=a)['ok']
    assert runtimes[111].running


def test_logout_expiry_and_failed_login_never_stop_trading(multiuser,monkeypatch):
    client,portal,runtimes=multiuser
    a=client.request('/login',payload=credentials())['session']
    b=client.request('/login',payload=credentials(222))['session']
    assert client.request('/logout',payload={},session=a)['ok']
    assert runtimes[111].running and runtimes[222].running
    assert client.request('/view',session=a)['expired']
    assert not client.request('/login',payload=dict(credentials(),password='wrong'))['ok']
    assert client.request('/view',session=b)['view']['private_account']==222
    created=portal.sessions[b][0]
    monkeypatch.setattr('core.account_portal.time.monotonic',lambda:created+portal.SESSION_SECONDS+1)
    for path in ['/view','/stop','/start','/logout']:
        assert client.request(path,session=b,**({'payload':{}} if path!='/view' else {}))['expired']
    assert all(runtime.running and runtime.stops==0 for runtime in runtimes.values())


def test_one_account_can_have_multiple_independent_browser_sessions(multiuser):
    client,_,runtimes=multiuser
    sessions=[client.request('/login',payload=credentials())['session'] for _ in range(2)]
    assert client.request('/logout',payload={},session=sessions[0])['ok']
    assert client.request('/status',session=sessions[1])['account']['login']==111
    assert runtimes[111].running


def test_dashboard_token_alone_cannot_shutdown_any_runtime(multiuser):
    client,_,runtimes=multiuser
    assert not client.request('/shutdown',payload={})['ok']
    assert all(runtime.running for runtime in runtimes.values())


def test_relogin_verifies_password_without_stopping_or_starting_engine(tmp_path,monkeypatch):
    runtime=DesktopRuntime(root=tmp_path)
    runtime.account={'login':111,'server':'LiteFinance-MT5-Demo','account_type':'demo'}
    runtime.started_at=123
    runtime.remember_password('correct-111')
    monkeypatch.setattr(runtime,'resolve_terminal_path',lambda request:'terminal64.exe')
    monkeypatch.setattr(runtime,'bridge_running',lambda:True)
    verified=[]
    monkeypatch.setattr(runtime,'authenticate_account',lambda request,path:verified.append(request.password))
    def forbidden(*args,**kwargs):raise AssertionError('Re-login must not touch engine or history')
    monkeypatch.setattr(runtime,'compose',forbidden)
    monkeypatch.setattr(runtime,'snapshot',forbidden)
    result=runtime.login(AccountLogin.parse(credentials()))
    assert verified==[] and result['started_at']==123
    with pytest.raises(AccountError):runtime.login(AccountLogin.parse(dict(credentials(),password='wrong')))
    assert not verified
    with pytest.raises(AccountError):runtime.login(AccountLogin.parse(credentials(222)))


def test_explicit_stop_survives_relogin_and_cannot_be_cleared_by_failed_start(tmp_path,monkeypatch):
    runtime=DesktopRuntime(root=tmp_path)
    runtime.account={'login':111,'server':'LiteFinance-MT5-Demo','account_type':'demo'}
    calls=[]
    monkeypatch.setattr(runtime,'compose',lambda *args,**kwargs:calls.append(args))
    runtime.stop_trading()
    assert runtime.stopped_flag().is_file() and calls==[('stop','trader')]
    monkeypatch.setattr(runtime,'bridge_running',lambda:False)
    with pytest.raises(AccountError):runtime.start_trading()
    assert runtime.stopped_flag().exists()
    # A new process sees the same stop preference; expiry/logout never reset it.
    assert DesktopRuntime(root=tmp_path).stopped_flag().exists()


@pytest.mark.parametrize('value',['../owner','0'*23,'g'*24,'0'*24+'\n',None])
def test_tenant_keys_reject_path_and_identifier_injection(value):
    with pytest.raises(AccountError):checked_key(value)


def test_public_login_cannot_select_a_terminal_or_unapproved_server():
    manager=TenantManager()
    for changes in [{'terminal_path':r'C:\other\terminal64.exe'},{'server':'LiteFinance-attacker'}]:
        with pytest.raises(AccountError):manager.for_account(AccountLogin.parse(dict(credentials(),**changes)))


def test_worker_cannot_invoke_gateway_actions_or_another_workers_compose(tmp_path,monkeypatch):
    monkeypatch.setattr(controller,'REGISTRY',tmp_path)
    key=tenant_key(111,'LiteFinance-MT5-Demo')
    folder=tmp_path/key;folder.mkdir()
    (folder/'record.json').write_text(json.dumps({'key':key,'uid':5001,'legacy':False}))
    control=controller.Controller(4000)
    for operation in ['login','status','view','stop','start']:
        with pytest.raises(AccountError):control.dispatch({'operation':operation,'tenant':key},5001)
    with pytest.raises(AccountError):control.dispatch({'operation':'compose','tenant':key,'args':['stop','trader']},5002)


@pytest.mark.parametrize('args',[['run','--privileged','trader'],['exec','trader','sh'],['up','-f','evil'],['stop','dashboard'],{'env':'unsafe'}])
def test_root_broker_rejects_arbitrary_compose_arguments(tmp_path,monkeypatch,args):
    monkeypatch.setattr(controller,'REGISTRY',tmp_path)
    control=controller.Controller(4000)
    with pytest.raises(AccountError):control.compose({'verified':True},args)


def test_unverified_enrolment_cannot_start_an_engine(tmp_path,monkeypatch):
    monkeypatch.setattr(controller,'REGISTRY',tmp_path)
    with pytest.raises(AccountError):controller.Controller(4000).compose({'verified':False},['up','-d','--no-build','trader'])


def test_cleanup_refuses_registered_accounts_and_any_other_path(tmp_path,monkeypatch):
    registry=tmp_path/'registry';registry.mkdir()
    homes=tmp_path/'homes';homes.mkdir()
    monkeypatch.setattr(controller,'REGISTRY',registry);monkeypatch.setattr(controller,'HOMES',homes)
    key=tenant_key(111,'LiteFinance-MT5-Demo')
    folder=registry/key;folder.mkdir()
    (folder/'record.json').write_text(json.dumps({'key':key,'uid':5001,'verified':True}))
    control=controller.Controller(4000)
    with pytest.raises(RuntimeError):control.discard_pending(key,homes/key)
    with pytest.raises(RuntimeError):control.discard_pending(key,tmp_path)
