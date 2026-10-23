from __future__ import annotations

from pathlib import Path

from loguru import logger

from utils.types import Direction, ExecutionResult, OrderRequest, TradingMode

try:
    import MetaTrader5 as mt5
except Exception:  # pragma: no cover
    mt5 = None


class ExecutionEngine:
    def __init__(self, client, mode: TradingMode, enable_live: bool, kill_switch_path: str) -> None:
        self.client = client
        self.mode = mode
        self.enable_live = enable_live
        self.kill_switch_path = Path(kill_switch_path)
        self.paper_tickets = 1_000_000

    def preflight(self, request: OrderRequest) -> tuple[bool, str]:
        if self.kill_switch_path.exists():
            return False, "Kill switch active"
        if self.mode == TradingMode.LIVE and not self.enable_live:
            return False, "ENABLE_LIVE_TRADING is false"
        if self.mode == TradingMode.LIVE and not self.client.health_check():
            return False, "MT5 connection unhealthy"
        if request.direction == Direction.HOLD or request.volume <= 0:
            return False, "Invalid order direction/volume"
        if request.sl <= 0 or request.tp <= 0:
            return False, "SL and TP are mandatory"
        return True, "OK"

    def execute(self, req: OrderRequest) -> ExecutionResult:
        ok, reason = self.preflight(req)
        if not ok:
            logger.warning("Order rejected: {}", reason)
            return ExecutionResult(False, None, None, reason)
        if self.mode in (TradingMode.PAPER, TradingMode.BACKTEST):
            self.paper_tickets += 1
            return ExecutionResult(True, self.paper_tickets, 0, f"{self.mode.value} fill", req.price)
        if mt5 is None:
            return ExecutionResult(False, None, None, "MetaTrader5 package unavailable")
        order_type = mt5.ORDER_TYPE_BUY if req.direction == Direction.BUY else mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": req.symbol,
            "volume": req.volume,
            "type": order_type,
            "price": req.price,
            "sl": req.sl,
            "tp": req.tp,
            "deviation": req.deviation,
            "magic": req.magic,
            "comment": req.comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        check = self.client.order_check(request)
        if check is None or getattr(check, "retcode", -1) not in (0, getattr(mt5, "TRADE_RETCODE_DONE", 10009)):
            return ExecutionResult(False, None, getattr(check, "retcode", None), f"order_check failed: {check}")
        result = self.client.order_send(request)
        if result is None:
            return ExecutionResult(False, None, None, "order_send returned None")
        done_codes = {getattr(mt5, "TRADE_RETCODE_DONE", 10009), getattr(mt5, "TRADE_RETCODE_PLACED", 10008)}
        success = int(result.retcode) in done_codes
        return ExecutionResult(success, getattr(result, "order", None) or getattr(result, "deal", None), int(result.retcode), getattr(result, "comment", ""), getattr(result, "price", None), result)
