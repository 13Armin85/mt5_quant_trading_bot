"""Gracefully release the trader and bridge without revoking owner auto-login."""
import os
import socket

from config import load_config
from core.mt5_remote import RemoteMT5Client
from core.server_control import compose_request


def main():
    result=compose_request(os.environ['NOA_DOCKER_CONTROL_SOCKET'],('stop','trader'))
    if result['returncode']:
        raise RuntimeError('Trader could not be stopped gracefully')
    try:
        with socket.create_connection(('127.0.0.1',18765),timeout=1):
            pass
    except OSError:
        return
    cfg=load_config()
    client=RemoteMT5Client(cfg.env.model_copy(update={'mt5_bridge_url':'http://127.0.0.1:18765',
        'mt5_login':None,'mt5_password':None,'mt5_server':None}),cfg.get('trading.mt5_time_zone','UTC'))
    try:
        client._rpc('stop_bridge')
    finally:
        client.http.close()
    print('Trader and bridge stopped; owner auto-login preference preserved.')


if __name__=='__main__':
    main()
