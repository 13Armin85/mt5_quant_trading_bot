"""Wait for native MT5 without submitting orders, then execute the requested CLI."""
import os
import signal
import sys
from threading import Event

from config import load_config
from core.mt5_remote import create_mt5_client


def main():
    args = sys.argv[1:] or ["run", "--mode", "live"]
    # Diagnostics/backtests stay read-only and should report connection errors promptly.
    if args[0] != "run":
        os.execv(sys.executable, [sys.executable, "main.py", *args])
    cfg = load_config(); cfg.validate_runtime()
    live = "live" in args or ("--mode" not in args and cfg.get("trading.mode") == "LIVE")
    if live and not cfg.env.enable_live_trading:
        raise SystemExit("LIVE blocked: set ENABLE_LIVE_TRADING=true in the Windows .env")
    if not cfg.env.mt5_bridge_url:
        raise SystemExit("Docker requires MT5_BRIDGE_URL pointing to the Windows bridge")
    stopped = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    while not stopped.is_set():
        client = None
        try:
            # Connect/claim/release only. This never calls order_send or the engine.
            client = create_mt5_client(cfg, trading=live)
            client.initialize()
            client.shutdown()
            if stopped.is_set():
                return
            os.execv(sys.executable, [sys.executable, "main.py", *args])
        except (OSError, RuntimeError):
            print("Waiting for Windows MT5 bridge / exclusive LIVE session...", flush=True)
        finally:
            if client is not None:
                client.shutdown()
        stopped.wait(10)


if __name__ == "__main__":
    main()
