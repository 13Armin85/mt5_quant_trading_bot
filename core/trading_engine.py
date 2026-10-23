from __future__ import annotations

import time
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone

from loguru import logger

from analysis.indicators import add_indicators
from analysis.market_structure import analyze_structure
from analysis.regime_detector import detect_regime
from analysis.volume import analyze_volume
from signals.signal_engine import SignalEngine
from core.position_manager import PositionManager
from core.symbol_resolver import SymbolResolver
from core.risk_manager import RiskState
from core.mt5_client import MT5Error
from core.execution import filling_mode
from signals.filters import spread_is_valid
from utils.types import Direction, OrderRequest, TradingMode


class TradingEngine:
    def __init__(self, app_cfg, client, feed, risk_manager, execution, repository, news_filter) -> None:
        self.cfg = app_cfg
        self.client = client
        self.feed = feed
        self.risk = risk_manager
        self.execution = execution
        self.repository = repository
        self.news_filter = news_filter
        self.signal_engine = SignalEngine(app_cfg)
        self.running = True
        self.resolver = SymbolResolver(client, app_cfg.symbols)
        self.position_manager = PositionManager(app_cfg.get("position_management", {}))
        self.resolved = {}
        self.frames_cache = {}
        stored = repository.get_state(execution.scope + ":risk")
        if stored:
            self.risk.state = RiskState(**stored)

    def persist_risk(self):
        if self.risk.state:
            self.repository.set_state(self.execution.scope + ":risk", asdict(self.risk.state))

    def run_forever(self) -> None:
        poll = float(self.cfg.get("trading.poll_seconds", 2))
        symbols = list(self.cfg.symbols.get("symbols", {}).keys())
        logger.info("Trading engine started symbols={}", symbols)
        try:
            while self.running:
                if not self.client.health_check():
                    self.client.reconnect(int(self.cfg.get("trading.reconnect_attempts", 3)), float(self.cfg.get("trading.reconnect_delay_seconds", 5)))
                try:
                    self.run_cycle(symbols)
                except (MT5Error, ValueError) as exc:
                    logger.warning("Runtime temporarily unavailable: {}", exc)
                    self.repository.set_state("heartbeat", {"time": datetime.now(timezone.utc).isoformat(), "connected": False, "mode": self.execution.mode.value, "last_decision": str(exc)})
                time.sleep(poll)
        except KeyboardInterrupt:
            logger.info("Stopped: broker positions retain their SL/TP; paper positions resume from journal")
        finally:
            self.persist_risk()

    def run_cycle(self, symbols):
        account = self.client.account_info()
        if account is None:
            raise ValueError("Account unavailable")
        equity = float(account.equity) if self.execution.mode == TradingMode.LIVE else self.paper_equity()
        self.risk.roll_state(equity)
        attempts = self.repository.attempts(self.execution.scope)
        today = datetime.now(timezone.utc).date().isoformat()
        self.risk.state.trades_today = max(self.risk.state.trades_today, sum(json.loads(a.payload)["created"][:10] == today and a.status != "REJECTED" for a in attempts))
        self.persist_risk()
        self.manage_positions()
        self.execution.reconcile()
        for symbol in symbols:
            try:
                self.process_symbol(symbol)
            except ValueError as exc:
                logger.warning("{} data/entry rejected: {}", symbol, exc)
                self.repository.set_state(self.execution.scope + ":decision", {"symbol":symbol,"reason":str(exc),"direction":"HOLD"})
        self.persist_risk()
        exposure, _, _ = self.risk.account_exposure()
        if self.execution.mode == TradingMode.PAPER:
            exposure += sum(p["risk"] for p in self.execution.paper_positions())
        status = {"time": datetime.now(timezone.utc).isoformat(), "connected": self.client.health_check(),
                  "mode": self.execution.mode.value, "symbols": self.resolved,
                  "equity": float(self.client.account_info().equity) if self.execution.mode == TradingMode.LIVE else self.paper_equity(),
                  "planned_open_risk": exposure,
                  "last_decision": self.repository.get_state(self.execution.scope + ":decision", "Waiting for data")}
        self.repository.set_state("heartbeat", status)
        logger.info("heartbeat {}", json.dumps(status))

    def process_symbol(self, logical_symbol: str):
        if self.execution.kill_switch_path.exists():
            logger.warning("Kill switch active")
            self.repository.set_state(self.execution.scope + ":decision", {"symbol":logical_symbol,"direction":"HOLD","reason":"Kill switch active"})
            return None
        if logical_symbol not in self.resolved:
            self.resolved[logical_symbol] = self.resolver.resolve(logical_symbol)
        symbol = self.resolved[logical_symbol]
        if not self.feed.is_tick_fresh(symbol):
            logger.warning("{} rejected: stale/missing tick", symbol)
            self.repository.set_state(self.execution.scope + ":decision", {"symbol":symbol,"direction":"HOLD","reason":"Stale/missing tick"})
            return None
        frames: dict[str, object] = {}
        tf_cfg = self.cfg.get("strategy.timeframes", {})
        for role, tf in tf_cfg.items():
            df = self.feed.bars(symbol, tf, int(self.cfg.get("trading.default_bars", 700)))
            if df.empty:
                logger.warning("{} rejected: no {} data", symbol, tf)
                return None
            cached = self.frames_cache.get((symbol, role))
            if cached is not None and cached.time.iloc[-1] == df.time.iloc[-1] and cached.close.iloc[-1] == df.close.iloc[-1]:
                frames[role] = cached
            else:
                frames[role] = add_indicators(df, self.cfg.get("strategy.indicators", {}))
                self.frames_cache[(symbol, role)] = frames[role]
        entry_df = frames["entry"]
        regime = detect_regime(frames["trend"], self.cfg.get("strategy.filters", {}))
        structure = analyze_structure(frames["setup"])
        volume = analyze_volume(frames["setup"], self.cfg.get("strategy.indicators", {}))
        news_ok, news_reason, news_score = self.news_filter.evaluate(logical_symbol)
        spread = self.feed.spread_points(symbol)
        historical = list(frames["entry"].get("spread", []))[-100:]
        spread_ok, _ = spread_is_valid(spread, historical, float(self.cfg.get("strategy.filters.max_spread_multiplier", 2)))
        if not spread_ok:
            news_ok, news_reason = False, "Abnormal spread; entry suspended"
        signal = self.signal_engine.generate(symbol, frames, regime, structure, volume, news_ok, news_reason, news_score, spread)
        if signal.direction != Direction.HOLD and not self.news_filter.direction_allowed(logical_symbol, signal.direction.value):
            signal.direction = Direction.HOLD
            signal.reasons.append("Configured economic surprise conflicts with technical direction")
        self.repository.save_signal(signal)
        decision = {"symbol": symbol, "direction": signal.direction.value, "score": signal.confidence, "candle": str(signal.candle_time), "reasons": signal.reasons}
        self.repository.set_state(self.execution.scope + ":decision", decision)
        logger.info("signal {}", json.dumps(decision, ensure_ascii=False))
        if signal.direction == Direction.HOLD:
            return signal
        if not news_ok:
            return signal
        account = self.client.account_info()
        if account is None:
            raise ValueError("Account unavailable")
        equity = float(account.equity) if self.execution.mode == TradingMode.LIVE else float(self.cfg.get("trading.paper_starting_balance", 10000)) + self.repository.get_state(self.execution.scope + ":paper_pnl", 0)
        exposure, positions, orders = self.risk.account_exposure()
        simulated = self.execution.paper_positions() if self.execution.mode == TradingMode.PAPER else []
        exposure += sum(p["risk"] for p in simulated)
        currencies = self.news_filter.currencies_for(logical_symbol)
        if any(not self.news_filter.currencies_for(p.symbol) for p in [*positions, *orders]):
            raise ValueError("Existing symbol has no currency/correlation mapping")
        shared = [p for p in [*positions, *orders] if currencies & self.news_filter.currencies_for(p.symbol)]
        shared += [p for p in simulated if currencies & self.news_filter.currencies_for(p["symbol"])]
        if len(shared) >= int(self.cfg.get("trading.max_correlated_positions", 1)):
            logger.info("{} rejected: shared currency exposure", symbol)
            return signal
        allowed, reason = self.risk.can_trade(equity, exposure / equity)
        if not allowed:
            logger.warning("{} rejected: {}", symbol, reason)
            return signal
        info = self.client.symbol_info(symbol)
        if info is None:
            return signal
        lot = self.risk.calculate_volume(symbol, signal.entry_price, signal.stop_loss, equity, info, exposure, signal.direction)
        if lot <= 0:
            logger.warning("{} rejected: calculated volume below broker minimum", symbol)
            return signal
        tick = self.client.symbol_info_tick(symbol)
        price = float(tick.ask if signal.direction == Direction.BUY else tick.bid)
        req = OrderRequest(symbol, signal.direction, lot, price, signal.stop_loss, signal.take_profit, int(self.cfg.get("trading.max_deviation_points", 20)), magic=int(self.cfg.get("trading.magic", 260912)), comment=signal.signal_id or "quant-bot", signal_id=signal.signal_id)
        result = self.execution.execute(req)
        self.repository.save_execution(signal, lot, result)
        if result.success:
            self.risk.state.trades_today += 1
        self.persist_risk()
        self.repository.set_state(self.execution.scope + ":decision", {**decision, "execution": result.message, "risk": result.planned_risk, "volume": result.filled_volume})
        logger.info("{} {} confidence={:.2f} result={}", symbol, signal.direction.value, signal.confidence, result.message)
        return signal

    def manage_positions(self):
        if self.execution.mode == TradingMode.PAPER:
            for p in self.execution.paper_positions():
                if not self.feed.is_tick_fresh(p["symbol"]):
                    continue
                tick = self.client.symbol_info_tick(p["symbol"])
                buy = p["direction"] == "BUY"
                current = float(tick.bid if buy else tick.ask)
                hit_sl = current <= p["sl"] if buy else current >= p["sl"]
                hit_tp = current >= p["tp"] if buy else current <= p["tp"]
                if hit_sl or hit_tp:
                    exit_price = current if hit_sl else p["tp"]
                    slip = float(self.cfg.get("trading.slippage_points", 0)) * self.client.symbol_info(p["symbol"]).point
                    exit_price += -slip if buy else slip
                    profit = self.client.order_calc_profit(0 if buy else 1, p["symbol"], p["volume"], p["price"], exit_price)
                    if profit is None or not math.isfinite(profit):
                        raise ValueError("Paper close calculation unavailable")
                    profit -= float(self.cfg.get("trading.commission_per_lot", 0)) * p["volume"]
                    self.repository.settle_paper(p["key"], self.execution.scope, p["ticket"], exit_price, profit)
                    self.risk.register_closed_trade(profit + p.get("partial_realized", 0))
                    self.persist_risk()
                    continue
                decision = self.position_manager.decide(p["direction"], p["price"], current, p["sl"], p["initial_risk"], self.latest_atr(p["symbol"]), p.get("partial_done", False))
                if decision.partial_close_fraction:
                    info = self.client.symbol_info(p["symbol"])
                    volume = self.risk._floor_step(p["volume"] * decision.partial_close_fraction, info.volume_step)
                    if volume >= info.volume_min and p["volume"] - volume >= info.volume_min - 1e-9:
                        profit = self.client.order_calc_profit(0 if buy else 1, p["symbol"], volume, p["price"], current)
                        if profit is None or not math.isfinite(profit):
                            raise ValueError("Paper partial-close calculation unavailable")
                        profit -= float(self.cfg.get("trading.commission_per_lot", 0)) * volume
                        p["risk"] *= (p["volume"] - volume) / p["volume"]
                        p["volume"] = round(p["volume"] - volume, 8)
                        p["partial_done"] = True
                        self.repository.partial_paper(p.pop("key"), self.execution.scope, p, profit)
                    continue
                if decision.new_sl is not None:
                    info = self.client.symbol_info(p["symbol"])
                    p["sl"] = round(round(decision.new_sl / info.trade_tick_size) * info.trade_tick_size, int(info.digits))
                    self.repository.update_attempt(p.pop("key"), "PAPER_OPEN", p)
            return
        if self.execution.mode != TradingMode.LIVE:
            return
        positions = self.client.positions_get()
        if positions is None:
            raise ValueError("Position management unavailable")
        journal = [dict(json.loads(a.payload), key=a.key) for a in self.repository.attempts(self.execution.scope) if a.status in {"OPEN", "PARTIAL", "PROTECTION_FAILURE"}]
        for p in positions:
            meta = next((a for a in journal if a.get("position_ticket") == int(p.ticket) and a["magic"] == int(p.magic)), None)
            if meta is None or not self.feed.is_tick_fresh(p.symbol):
                continue
            if not p.sl:
                self.execution.close_position(p, int(p.magic))
                raise RuntimeError("Bot position without SL; emergency close attempted")
            info, tick = self.client.symbol_info(p.symbol), self.client.symbol_info_tick(p.symbol)
            current = float(tick.bid if int(p.type) == 0 else tick.ask)
            decision = self.position_manager.decide("BUY" if int(p.type) == 0 else "SELL", float(p.price_open), current, float(p.sl), meta["initial_risk"], self.latest_atr(p.symbol), meta.get("partial_state") is not None)
            if decision.partial_close_fraction:
                volume = self.risk._floor_step(float(p.volume) * decision.partial_close_fraction, info.volume_step)
                if volume >= info.volume_min and float(p.volume) - volume >= info.volume_min - 1e-9:
                    meta["partial_state"] = "SUBMITTING"
                    key = meta.pop("key")
                    self.repository.update_attempt(key, "OPEN", meta)
                    result = self.client.order_send({"action":1,"position":int(p.ticket),"symbol":p.symbol,"volume":volume,
                        "type":1 if int(p.type)==0 else 0,"price":current,"magic":int(p.magic),"comment":"quant-partial",
                        "deviation":int(self.cfg.get("trading.max_deviation_points",20)),"type_time":0,"type_filling":filling_mode(info)})
                    remaining = self.client.positions_get(symbol=p.symbol)
                    match = next((x for x in remaining or () if x.ticket == p.ticket), None)
                    if result is not None and int(result.retcode) in (10009,10010) and match is not None and float(match.volume) < float(p.volume):
                        meta.update(partial_state="DONE", volume=float(match.volume))
                    elif result is not None and int(result.retcode) not in (10008,10009,10010,10012,10031):
                        meta["partial_state"] = "REJECTED"
                    else:
                        meta["partial_state"] = "UNKNOWN"
                    self.repository.update_attempt(key, "OPEN", meta)
                continue
            if decision.new_sl is None:
                continue
            candidate = round(round(decision.new_sl / info.trade_tick_size) * info.trade_tick_size, int(info.digits))
            improves = candidate > p.sl if int(p.type) == 0 else candidate < p.sl
            minimum = max(info.trade_stops_level, info.trade_freeze_level) * info.point
            if not improves or abs(current - candidate) <= minimum or (candidate >= current if int(p.type) == 0 else candidate <= current):
                continue
            result = self.client.order_send({"action": 6, "position": int(p.ticket), "symbol": p.symbol, "sl": candidate, "tp": float(p.tp), "magic": int(p.magic)})
            logger.info("SL management ticket={} retcode={}", p.ticket, getattr(result, "retcode", None))

    def latest_atr(self, symbol):
        frame = self.frames_cache.get((symbol, "entry"))
        return float(frame.atr.iloc[-1]) if frame is not None else None

    def paper_equity(self):
        equity = float(self.cfg.get("trading.paper_starting_balance", 10000)) + self.repository.get_state(self.execution.scope + ":paper_pnl", 0)
        for p in self.execution.paper_positions():
            tick = self.client.symbol_info_tick(p["symbol"])
            if tick is None:
                raise ValueError("Paper mark-to-market tick unavailable")
            price = tick.bid if p["direction"] == "BUY" else tick.ask
            pnl = self.client.order_calc_profit(0 if p["direction"] == "BUY" else 1, p["symbol"], p["volume"], p["price"], price)
            if pnl is None or not math.isfinite(pnl):
                raise ValueError("Paper mark-to-market unavailable")
            equity += pnl - float(self.cfg.get("trading.commission_per_lot", 0)) * p["volume"]
        return equity
