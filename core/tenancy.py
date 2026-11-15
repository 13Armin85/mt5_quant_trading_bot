"""Account-bound capabilities for the private multi-tenant controller.

No caller can supply a filesystem path, port, Linux user, Compose environment,
or executable. A browser session keeps one runtime capability for its lifetime.
"""
import hashlib
import json
import os
import re
import socket

from core.account_login import AccountError, SERVERS

MAX_REPLY = 4 * 1024 * 1024


def tenant_key(login, server):
    if type(login) is not int or not 0 < login < 10**18 or server not in SERVERS.values():
        raise AccountError('شماره حساب یا سرور LiteFinance معتبر نیست.')
    return hashlib.sha256(f'{server}:{login}'.encode()).hexdigest()[:24]


def checked_key(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{24}', value):
        raise AccountError('شناسهٔ محیط معتبر نیست.')
    return value


def control(request):
    address = os.environ.get('NOA_TENANT_CONTROL_SOCKET', '/run/noatech-tenants/control.sock')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(290)
            connection.connect(address)
            connection.sendall(json.dumps(request, ensure_ascii=True).encode()+b'\n')
            with connection.makefile('rb') as handle:
                raw = handle.readline(MAX_REPLY+1)
        if len(raw)>MAX_REPLY or not raw.endswith(b'\n'):
            raise ValueError('Invalid control response')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('Invalid control response')
        if not result.get('ok'):
            raise AccountError(result.get('error', 'محیط معاملاتی در دسترس نیست.'))
        return result
    except (OSError, ValueError) as error:
        if isinstance(error, AccountError):
            raise
        raise AccountError('سرویس محیط معاملاتی پاسخ نداد؛ کمی بعد دوباره تلاش کنید.') from None


class TenantRuntime:
    def __init__(self, key):
        self.key = checked_key(key)
        self.backend_session = None

    def call(self, operation, **values):
        return control(dict(operation=operation, tenant=self.key,
            session=self.backend_session, **values))

    def login(self, request):
        result = self.call('login', payload={'login':str(request.login), 'password':request.password,
            'server':request.server, 'account_type':request.account_type,
            'confirm_real':request.account_type=='real', 'terminal_path':request.terminal_path})
        self.backend_session = result['session']
        return result['account']

    def status(self):
        result = self.call('status')
        return dict(result, view_api=True)

    def view(self):
        return self.call('view')['view']

    def stop_trading(self):
        self.call('stop')

    def start_trading(self):
        self.call('start')


class TenantManager:
    def policy(self):
        return {'real_trading': False, 'multi_user': True}

    def for_account(self, request):
        if request.server != SERVERS[request.account_type] or request.terminal_path:
            raise AccountError('در محیط چندکاربره فقط سرور رسمی و متاتریدر اختصاصی سامانه مجاز است.')
        return TenantRuntime(tenant_key(request.login, request.server))
