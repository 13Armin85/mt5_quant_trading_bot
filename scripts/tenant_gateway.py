"""Unprivileged browser-session gateway. Contains no MT5 or Docker credentials."""
import os
from pathlib import Path
from http.server import ThreadingHTTPServer
from threading import BoundedSemaphore

from core.account_portal import AccountPortal, handler_for
from core.tenancy import TenantManager


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32
    slots = BoundedSemaphore(32)

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

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address


def main():
    token = Path(os.environ['NOA_GATEWAY_TOKEN_FILE']).read_text().strip()
    portal = AccountPortal(token, TenantManager())
    with GatewayServer(('127.0.0.1',18767), handler_for(portal)) as server:
        server.serve_forever()


if __name__=='__main__':
    main()
