"""Root-only provisioning broker; never exposed to HTTP or browser clients.

All paths, Linux identities, units, images and Compose fields are derived here.
The gateway and tenant workers authenticate with Unix SO_PEERCRED. Each worker
can operate only its own fixed trader. Passwords are encrypted with systemd.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import socketserver
import sqlite3
import struct
import subprocess
from threading import Lock, BoundedSemaphore
import time

import httpx

from core.account_login import AccountError, AccountLogin, SERVERS
from core.tenancy import MAX_REPLY, checked_key, tenant_key

APP=Path('/opt/noatech/app')
CODE=Path('/opt/noatech/tenant-app')
TEMPLATE=Path('/opt/noatech/tenant-template')
REGISTRY=Path('/etc/noatech/tenants')
HOMES=Path('/var/lib/noatech-tenants')
POLICIES=Path('/etc/noatech-worker-policies')
OPERATIONS={('ps','--status','running','--services'),('stop','trader'),
    ('start','trader'),('up','-d','--no-build','trader')}


def atomic_json(path, value):
    temporary=path.with_name(path.name+'.'+secrets.token_hex(12))
    try:
        with temporary.open('x',encoding='utf-8') as handle:
            os.chmod(temporary,0o600)
            json.dump(value,handle)
            handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def run(command, **kwargs):
    return subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,check=True,timeout=kwargs.pop('timeout',60),**kwargs)


class Controller:
    def __init__(self, gateway_uid):
        self.gateway_uid=gateway_uid
        self.registry_lock=Lock()
        self.locks={}
        self.admission=BoundedSemaphore(1)
        self.allowed_uids={gateway_uid}
        for path in REGISTRY.glob('*/record.json'):
            record=json.loads(path.read_text())
            if not record.get('legacy'):
                self.allowed_uids.add(record['uid'])

    def record(self,key):
        checked_key(key)
        path=REGISTRY/key/'record.json'
        if not path.is_file():
            raise AccountError('محیط حساب یافت نشد؛ دوباره وارد شوید.')
        record=json.loads(path.read_text())
        if record['key']!=key:
            raise AccountError('پیکربندی محیط معتبر نیست.')
        return record

    def tenant_lock(self,key):
        with self.registry_lock:
            return self.locks.setdefault(key,Lock())

    def forward(self,record,operation,session=None,payload=None):
        token=Path(record['portal_token']).read_text().strip()
        headers={'Authorization':'Bearer '+token}
        if session:
            headers['X-Account-Session']=session
        address=f"http://127.0.0.1:{record['portal_port']}/{operation}"
        with httpx.Client(trust_env=False,timeout=230 if operation=='login' else 210) as client:
            method='GET' if operation in {'status','view'} else 'POST'
            options={} if method=='GET' else {'json':payload or {}}
            with client.stream(method,address,headers=headers,**options) as response:
                body=bytearray()
                for chunk in response.iter_bytes(65536):
                    body.extend(chunk)
                    if len(body)>MAX_REPLY:
                        raise AccountError('حجم پاسخ محیط حساب بیش از حد مجاز است.')
        result=json.loads(body)
        if not result.get('ok'):
            raise AccountError(result.get('error','اتصال حساب کامل نشد.'))
        return result

    def encrypt(self,record,payload):
        target=Path(record['credential'])
        pending=target.with_name('credential-'+secrets.token_hex(12))
        try:
            subprocess.run(['systemd-creds','encrypt','--with-key=host','--name=mt5','-',str(pending)],
                input=json.dumps(payload).encode(),capture_output=True,check=True,timeout=15)
            os.chmod(pending,0o600)
            os.replace(pending,target)
        finally:
            pending.unlink(missing_ok=True)

    def wait_worker(self,record):
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            try:
                self.forward(record,'status')
                return
            except (httpx.HTTPError,ValueError):
                state=subprocess.run(['systemctl','is-failed',record['unit']],capture_output=True,text=True,timeout=5)
                if state.returncode==0:
                    raise AccountError('اتصال متاتریدر انجام نشد؛ اطلاعات حساب و سرور را بررسی کنید.')
                time.sleep(1)
        raise AccountError('آماده‌سازی متاتریدر طول کشید؛ کمی بعد دوباره وارد شوید.')

    def capacity(self):
        policy=json.loads(Path('/etc/noatech/tenants-policy.json').read_text())
        records=[json.loads(path.read_text()) for path in REGISTRY.glob('*/record.json')]
        if len(records)>=int(policy['max_accounts']):
            raise AccountError('ظرفیت حساب‌های مستقل این سرور تکمیل است؛ برای کاربر بیشتر باید منابع افزایش یابد.')
        memory={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()}
        if memory['MemAvailable']<int(policy.get('minimum_available_mb',768))*1024:
            raise AccountError('منابع آزاد برای متاتریدر مستقل کافی نیست؛ حساب‌های فعال متوقف نمی‌شوند.')
        return next(slot for slot in range(1,int(policy['max_accounts'])+1) if slot not in {r['slot'] for r in records})

    def provision(self,request,key):
        import pwd
        slot=self.capacity()
        private=REGISTRY/key
        private.mkdir(mode=0o700)
        username=f'noaworker{slot}'
        home=HOMES/key
        try:
            user=pwd.getpwnam(username)
            if user.pw_dir!=f'/var/lib/noatech-slots/{slot}' or user.pw_uid==0 or user.pw_shell!='/usr/sbin/nologin':
                raise AccountError('هویت سیستم‌عامل محیط معتبر نیست.')
            home.mkdir(mode=0o700,parents=True,exist_ok=True)
            os.chown(home,user.pw_uid,user.pw_gid)
            work=home/'work'
            for folder in ['work','work/.docker','work/.docker/data','work/.docker/control','work/logs','work/reports','state','startup','wine']:
                path=home/folder;path.mkdir(parents=True,exist_ok=True)
                os.chown(path,user.pw_uid,user.pw_gid);os.chmod(path,0o2770 if folder.startswith('work/') else 0o700)
            policy=POLICIES/key;policy.mkdir(mode=0o750)
            os.chown(policy,0,user.pw_gid)
            tokens={}
            for name in ['bridge','portal']:
                path=policy/(name+'.token');path.write_text(secrets.token_urlsafe(48))
                os.chown(path,0,user.pw_gid);os.chmod(path,0o640);tokens[name]=str(path)
            python=home/'wine/drive_c/Python312'
            shutil.copytree(TEMPLATE/'Python312',python,symlinks=False)
            (python/'python312._pth').write_text('python312.zip\n.\nLib\\site-packages\nZ:\\opt\\noatech\\tenant-app\nimport site\n')
            terminal=home/'wine/drive_c/Program Files/MetaTrader 5'
            (terminal/'Config').mkdir(parents=True)
            for name in ['terminal64.exe','Config/servers.dat']:
                shutil.copyfile(TEMPLATE/'MetaTrader5'/name,terminal/name)
            # Only public executables, Python packages and the server catalogue
            # are copied. No account caches, histories, registry or owner files.
            for directory,dirs,files in os.walk(home/'wine'):
                for name in [*dirs,*files]:
                    path=Path(directory)/name;os.chown(path,user.pw_uid,user.pw_gid)
            authority=home/'.Xauthority';authority.touch(mode=0o600)
            subprocess.run(['xauth','-f',str(authority),'source','-'],input=f'add :{100+slot} . {secrets.token_hex(16)}\n'.encode(),capture_output=True,check=True)
            os.chown(authority,user.pw_uid,user.pw_gid)
            record={'key':key,'slot':slot,'login':request.login,'server':request.server,'account_type':request.account_type,
                'uid':user.pw_uid,'gid':user.pw_gid,'username':username,'home':str(home),'work':str(work),
                'portal_token':tokens['portal'],'bridge_token':tokens['bridge'],'credential':str(private/'mt5.cred'),
                'portal_port':19000+slot*2,'bridge_port':19001+slot*2,'display':100+slot,
                'unit':f'noatech-account-{key}.service','verified':False,'legacy':False}
            atomic_json(private/'record.json',record)
            self.allowed_uids.add(user.pw_uid)
            (work/'.docker/control/trading_stopped').touch(mode=0o600)
            self.write_compose(record)
            self.write_unit(record)
            return record
        except Exception:
            # All of these are new, unverified provisioning artifacts; there is
            # never a live engine or registered user's journal in this branch.
            self.discard_pending(key,home)
            raise

    def discard_pending(self,key,home):
        checked_key(key)
        expected=(HOMES/key).resolve()
        if home.resolve()!=expected or expected.parent!=HOMES.resolve():
            raise RuntimeError('Invalid pending cleanup path')
        record_path=REGISTRY/key/'record.json'
        if record_path.exists():
            record=json.loads(record_path.read_text())
            if record.get('verified') or record.get('legacy'):
                raise RuntimeError('Registered environments must never be deleted automatically')
            subprocess.run(['systemctl','stop',record['unit']],capture_output=True,timeout=60)
            Path('/etc/systemd/system',record['unit']).unlink(missing_ok=True)
        if expected.exists():shutil.rmtree(expected)
        if (POLICIES/key).exists():shutil.rmtree(POLICIES/key)
        if (REGISTRY/key).exists():shutil.rmtree(REGISTRY/key)

    def write_compose(self,record):
        work=record['work']
        live=record['account_type']=='demo' and record['verified']
        service={'image':'noatech:server','user':f"10001:{record['gid']}",'init':True,
            'restart':'unless-stopped','stop_grace_period':'180s','network_mode':'host',
            'command':['run','--mode','live' if live else 'paper'],
            'environment':{'MT5_BRIDGE_URL':f"http://127.0.0.1:{record['bridge_port']}",
                'MT5_BRIDGE_TOKEN_FILE':'/run/secrets/bridge.token','MT5_LOGIN':str(record['login']),
                'MT5_SERVER':record['server'],'ENABLE_LIVE_TRADING':str(live).lower(),
                'DATABASE_URL':'sqlite:////app/data/trading_bot.db','NEWS_CACHE_DIR':'/app/data/news_cache',
                'RUNTIME_LOCK_PATH':'/app/data/.bot.lock','KILL_SWITCH_PATH':'/app/control/kill_switch.flag',
                'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'},
            'volumes':[f'{work}/.docker/data:/app/data',f'{work}/.docker/control:/app/control',
                f'{work}/logs:/app/logs',f'{work}/reports:/app/reports',f'{CODE}/config:/app/config:ro'],
            'secrets':[{'source':'bridge_token','target':'bridge.token'}],'read_only':True,'tmpfs':['/tmp'],'cap_drop':['ALL'],
            'security_opt':['no-new-privileges:true'],'pids_limit':192,'mem_limit':'512m','cpus':.4,
            'ulimits':{'core':{'soft':0,'hard':0}},
            'healthcheck':{'test':['CMD','python','-m','docker_runtime.healthcheck'],'interval':'30s',
                'timeout':'10s','start_period':'180s','retries':3},
            'logging':{'driver':'json-file','options':{'max-size':'10m','max-file':'3'}}}
        atomic_json(REGISTRY/record['key']/'compose.json',{'name':'noa-'+record['key'],'services':{'trader':service},
            'secrets':{'bridge_token':{'file':record['bridge_token']}}})

    def write_unit(self,record):
        home=record['home'];key=record['key'];work=record['work']
        env={'HOME':home,'WINEPREFIX':home+'/wine','WINEDEBUG':'-all',
            'NOA_WINE':'/opt/wine-devel/bin/wine','NOA_WINE_PYTHON':home+'/wine/drive_c/Python312/python.exe',
            'WINEDLLOVERRIDES':'ucrtbase=n,b;winedbg.exe=d','DISPLAY':':'+str(record['display']),
            'XAUTHORITY':home+'/.Xauthority','PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1',
            'NOA_TENANT_ID':key,'NOA_TENANT_LOGIN':str(record['login']),
            'NOA_TENANT_SERVER':record['server'],'NOA_TENANT_TYPE':record['account_type'],
            'NOA_RUNTIME_ROOT':work,'NOA_HOST_STATE_DIR':home+'/state','NOA_MT5_STARTUP_DIR':'/run/noa-account-'+key,
            'NOA_AUTO_LOGIN_FLAG':work+'/.docker/control/auto_login','NOA_TRADING_STOPPED_FLAG':work+'/.docker/control/trading_stopped',
            'NOA_DEMO_TRADING_FLAG':str(POLICIES/key/'live.enabled'),'MT5_PORTABLE':'true',
            'MT5_BRIDGE_TOKEN_FILE':record['bridge_token'],'ACCOUNT_PORTAL_TOKEN_FILE':record['portal_token'],
            'NOA_MT5_BRIDGE_PORT':str(record['bridge_port']),'NOA_ACCOUNT_PORTAL_PORT':str(record['portal_port']),
            'RUNTIME_LOCK_PATH':home+'/state/mt5.lock','DATABASE_URL':'sqlite:///'+work+'/.docker/data/trading_bot.db',
            'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'}
        environment='\n'.join('Environment="'+name+'='+value+'"' for name,value in env.items())
        unit=f'''[Unit]
Description=NOA isolated account {key}
Wants=noatech-tenants.service
After=noatech-tenants.service network-online.target
[Service]
Type=exec
User={record['username']}
Group={record['username']}
WorkingDirectory={CODE}
RuntimeDirectory=noa-account-{key}
RuntimeDirectoryMode=0700
{environment}
LoadCredentialEncrypted=mt5:{record['credential']}
ExecStart=/opt/noatech/venv/bin/python -m scripts.tenant_worker
ExecStop=/opt/noatech/venv/bin/python -m scripts.tenant_stop
Restart=on-failure
RestartSec=20
TimeoutStopSec=240
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths={home} /run/noa-account-{key}
MemoryHigh=640M
MemoryMax=896M
MemorySwapMax=512M
TasksMax=256
LimitCORE=0
[Install]
WantedBy=multi-user.target
'''
        Path('/etc/systemd/system',record['unit']).write_text(unit)
        run(['systemctl','daemon-reload'])

    def compose(self,record,args):
        if not isinstance(args,list) or any(not isinstance(a,str) for a in args) or tuple(args) not in OPERATIONS:
            raise AccountError('عملیات موتور مجاز نیست.')
        if not record['verified'] and args[0] in {'up','start'}:
            raise AccountError('حساب هنوز با بروکر تأیید نشده است.')
        result=subprocess.run(['/usr/bin/docker','compose','--env-file','/dev/null','-f',
            str(REGISTRY/record['key']/'compose.json'),*args],cwd=CODE,
            env={'PATH':'/usr/bin:/bin','HOME':'/var/empty','DOCKER_CONFIG':'/etc/noatech/docker-cli'},
            capture_output=True,text=True,timeout=210)
        return {'ok':True,'returncode':result.returncode,'stdout':result.stdout[-18000:]}

    def dispatch(self,request,peer_uid):
        if not isinstance(request,dict) or len(request)>6:
            raise AccountError('درخواست معتبر نیست.')
        key=checked_key(request.get('tenant'))
        operation=request.get('operation')
        if operation=='compose':
            record=self.record(key)
            if peer_uid!=record['uid'] or record['legacy']:
                raise AccountError('دسترسی محیط مجاز نیست.')
            return self.compose(record,request.get('args'))
        if peer_uid!=self.gateway_uid:
            raise AccountError('دسترسی مجاز نیست.')
        if operation not in {'login','status','view','stop','start'}:
            raise AccountError('عملیات پشتیبانی نمی‌شود.')
        if operation!='login':
            return self.forward(self.record(key),operation,request.get('session'))
        parsed=AccountLogin.parse(request.get('payload'))
        if parsed.terminal_path or parsed.server!=SERVERS[parsed.account_type] or tenant_key(parsed.login,parsed.server)!=key:
            raise AccountError('هویت حساب یا سرور معتبر نیست.')
        with self.tenant_lock(key):
            pending_record=None
            if (REGISTRY/key/'record.json').exists():
                record=self.record(key)
                if record.get('verified'):
                    result=self.forward(record,'login',payload=request['payload'])
                    self.encrypt(record,request['payload'])
                    return result
                # Recover an interrupted first enrolment. No trading can have
                # started: compose() rejects every start until verified=True.
                pending_record=record
            if not self.admission.acquire(blocking=False):
                raise AccountError('یک محیط جدید در حال آماده‌سازی است؛ کمی بعد دوباره تلاش کنید.')
            record=None
            try:
                record=pending_record or self.provision(parsed,key)
                if pending_record:
                    run(['systemctl','stop',record['unit']],timeout=240)
                    self.write_compose(record)
                    self.write_unit(record)
                self.encrypt(record,request['payload'])
                run(['systemctl','start',record['unit']])
                self.wait_worker(record)
                result=self.forward(record,'login',payload=request['payload'])
                account=result['account']
                if (account['login'],account['server'],account['account_type'])!=(parsed.login,parsed.server,parsed.account_type):
                    raise AccountError('حساب تأییدشده تطابق ندارد.')
                if parsed.account_type=='demo' and (not account.get('account_trade_allowed') or
                    not account.get('expert_allowed') or account.get('python_api_disabled')):
                    raise AccountError('برای ترید دمو از رمز معاملاتی و مجوز فعال معامله استفاده کنید؛ رمز سرمایه‌گذار کافی نیست.')
                run(['systemctl','stop',record['unit']],timeout=240)
                record['verified']=True
                atomic_json(REGISTRY/key/'record.json',record)
                if parsed.account_type=='demo':
                    (POLICIES/key/'live.enabled').touch(mode=0o600)
                self.write_compose(record)
                (Path(record['work'])/'.docker/control/trading_stopped').unlink()
                run(['systemctl','enable','--now',record['unit']])
                self.wait_worker(record)
                return self.forward(record,'login',payload=request['payload'])
            except Exception:
                if record and not record.get('verified'):
                    self.discard_pending(key,Path(record['home']))
                raise
            finally:
                self.admission.release()


class ControlServer(socketserver.ThreadingMixIn, getattr(socketserver,'UnixStreamServer',socketserver.TCPServer)):
    daemon_threads=True
    request_queue_size=32
    slots=BoundedSemaphore(32)

    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:super().process_request(request,address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()

    def verify_request(self,request,address):
        _,uid,_=struct.unpack('3i',request.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        return uid in self.controller.allowed_uids


def main():
    if os.name!='posix':
        raise SystemExit('Tenant provisioning requires Linux Unix sockets and systemd.')
    import pwd
    controller=Controller(pwd.getpwnam('noagate').pw_uid)
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(10)
            _,uid,_=struct.unpack('3i',self.request.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            raw=self.rfile.readline(16385)
            try:
                if len(raw)>16384 or not raw.endswith(b'\n'):
                    raise AccountError('درخواست معتبر نیست.')
                result=controller.dispatch(json.loads(raw),uid)
            except AccountError as error:
                result={'ok':False,'error':str(error)}
            except Exception:
                result={'ok':False,'error':'محیط حساب آماده نشد؛ اطلاعات ورود و ظرفیت سرور را بررسی کنید.'}
            body=json.dumps(result,ensure_ascii=True).encode()+b'\n'
            if len(body)>MAX_REPLY:
                body=b'{"ok":false,"error":"Account view is too large"}\n'
            try:self.wfile.write(body)
            except OSError:pass
    address='/run/noatech-tenants/control.sock'
    Path(address).unlink(missing_ok=True)
    os.umask(0o007)
    with ControlServer(address,Handler) as server:
        server.controller=controller
        os.chmod(address,0o660)  # Dedicated group plus kernel peer identity, checked per operation.
        server.serve_forever()


if __name__=='__main__':
    main()
