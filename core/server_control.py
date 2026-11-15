"""Bounded local protocol for the server's restricted Compose controller."""
import json
import socket

MAX_CONTROL_BYTES = 65536


def compose_request(path, args):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(230)
        connection.connect(path)
        connection.sendall(json.dumps(list(args)).encode() + b'\n')
        with connection.makefile('rb') as handle:
            raw = handle.readline(MAX_CONTROL_BYTES + 1)
        if len(raw) > MAX_CONTROL_BYTES or not raw.endswith(b'\n'):
            raise ValueError('Invalid controller response')
        result = json.loads(raw)
        if not isinstance(result, dict) or type(result.get('returncode')) is not int:
            raise ValueError('Invalid controller response')
        return result
