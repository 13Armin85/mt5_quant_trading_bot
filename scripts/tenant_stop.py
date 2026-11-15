"""Maintenance stop; preserves the account's durable trading preference."""
from pathlib import Path
import os
import socket

from config import load_config
from scripts.tenant_worker import TenantWineRuntime


def main():
    runtime=TenantWineRuntime(load_config(),root=Path(os.environ['NOA_RUNTIME_ROOT']))
    runtime.compose('stop','trader')
    try:
        with socket.create_connection(('127.0.0.1',runtime.bridge_port()),timeout=1):
            pass
    except OSError:
        return
    runtime.stop_bridge()


if __name__=='__main__':
    main()
