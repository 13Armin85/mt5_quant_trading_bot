from __future__ import annotations

import json
import math
from threading import Event
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from loguru import logger

from analysis.indicators import add_indicators
from analysis.market_structure import analyze_structure
from analysis.regime_detector import detect_regime
from analysis.volume import analyze_volume
from signals.signal_engine import SignalEngine
from core.position_manager import PositionManager
from core.symbol_resolver import SymbolResolver
from core.risk_manager import RiskState
from core.correlation import CorrelationGuard
from core.mt5_client import MT5Error
from core.execution import filling_mode
from signals.trade_plan import pricing_context
from monitoring.decisions import config_revision, evaluation_id, reason_code
from analysis.sessions import active_sessions
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
        self.stop_event = Event()
        self.resolver = SymbolResolver(client, app_cfg.symbols)
        self.position_manager = PositionManager(app_cfg.get("position_management", {}))
        self.resolved = {}
        self.frames_cache = {}
        self.quote_statuses = {}
        self.revision = config_revision(app_cfg)
        self.risk.symbols = app_cfg.symbols
        self.risk.mode = execution.mode.value
        self.correlation = CorrelationGuard(float(app_cfg.get("trading.correlation_threshold", .8)),
            int(app_cfg.get("trading.max_correlated_positions", 1)),
            int(app_cfg.get("trading.correlation_minimum_history", 60)), app_cfg.symbols)
        self.execution.portfolio_check = self.portfolio_check
        self.execution.before_send = self.before_send
        stored = repository.get_state(execution.scope + ":risk")
        if stored:
            self.risk.state = RiskState(**stored)

    def persist_risk(self):
        if self.risk.state:
            self.repository.set_state(self.execution.scope + ":risk", asdict(self.risk.state))

    def stop(self) -> None:
        self.running = False
        self.stop_event.set()

    def run_forever(self) -> None:
        poll = float(self.cfg.get("trading.poll_seconds", 2))
        symbols = list(self.cfg.symbols.get("symbols", {}).keys())
        logger.info("Trading engine started symbols={}", symbols)
        try:
            if hasattr(self.news_filter, "start_background"):
                self.news_filter.start_background()
            while self.running:
                try:
                    if not self.client.health_check():
                        self.client.reconnect(int(self.cfg.get("trading.reconnect_attempts", 3)), float(self.cfg.get("trading.reconnect_delay_seconds", 5)))
                    self.run_cycle(symbols)
                except (MT5Error, ValueError, TimeoutError, ConnectionError) as exc:
                    logger.warning("Runtime temporarily unavailable: {}", exc)
                    self.repository.set_state("heartbeat", {"time": datetime.now(timezone.utc).isoformat(), "connected": False, "mode": self.execution.mode.value, "last_decision": str(exc)})
                self.stop_event.wait(poll)
        except KeyboardInterrupt:
            logger.info("Stopped: broker positions retain their SL/TP; paper positions resume from journal")
        finally:
            if hasattr(self.news_filter, "stop_background"):
                self.news_filter.stop_background()
            self.persist_risk()
            logger.info("Engine stopped; broker positions retain their SL/TP")

    def run_cycle(self, symbols):
        account = self.client.account_info()
        if account is None:
            raise ValueError("Account unavailable")
        equity = float(account.equity) if self.execution.mode == TradingMode.LIVE else self.paper_equity()
        self.risk.roll_state(equity)
        attempts = self.repository.attempts(self.execution.scope)
        today = self.risk.clock().date().isoformat()
        self.risk.state.trades_today = max(self.risk.state.trades_today, sum(json.loads(a.payload)["created"][:10] == today and a.status != "REJECTED" for a in attempts))
        self.persist_risk()
        self.manage_positions()
        self.execution.reconcile()
        resume = self.repository.get_state(self.execution.scope + ":resume_request")
        if resume and self.risk.state.last_resume_request != resume["id"]:
            exposure, _, _ = self.risk.account_exposure()
            if self.execution.mode == TradingMode.PAPER:
                exposure += sum(p["risk"] for p in self.execution.paper_positions())
            equity = float(self.client.account_info().equity) if self.execution.mode == TradingMode.LIVE else self.paper_equity()
            ok, reason = self.risk.request_resume(resume["id"],equity,exposure/equity,self.execution.kill_switch_path.exists())
            self.repository.set_state(self.execution.scope + ":resume_result", {**resume,"accepted":ok,"result":reason})
            self.persist_risk()
        # News polling also runs when every instrument has a closed/stale market.
        self.news_filter.refresh()
        candidates = []
        for symbol in sorted(symbols):
            if not self.running:
                break
            try:
                signal = self.process_symbol(symbol, submit=False)
                if signal is not None and signal.direction != Direction.HOLD:
                    candidates.append((symbol, signal))
            except (ValueError, MT5Error, TimeoutError, ConnectionError) as exc:
                logger.warning("{} data/entry rejected: {}", symbol, exc)
                self.record_unavailable(self.resolved.get(symbol,symbol),str(exc),reason_code(exc))
        # All proposals exist before any entry. Ties are resolved by name, never scan order.
        candidates.sort(key=lambda item: (-item[1].confidence, -(item[1].risk_reward or 0), item[1].symbol, item[1].strategy))
        for logical, signal in candidates:
            if not self.running:
                break
            try:
                self.submit_signal(logical, signal)
            except (ValueError, MT5Error, TimeoutError, ConnectionError) as exc:
                self.record_decision(signal.meta["runtime_decision"], "risk", str(exc))
        self.persist_risk()
        exposure, _, _ = self.risk.account_exposure()
        if self.execution.mode == TradingMode.PAPER:
            exposure += sum(p["risk"] for p in self.execution.paper_positions())
        now=datetime.now(timezone.utc)
        clocks={"utc":now.isoformat(),"server":now.astimezone(getattr(self.client,"market_timezone",timezone.utc)).isoformat(),
                "tehran":now.astimezone(ZoneInfo("Asia/Tehran")).isoformat()}
        status = {"time": now.isoformat(), "connected": self.client.health_check(),"clocks":clocks,
                  "mode": self.execution.mode.value, "symbols": self.resolved,
                  "scope":self.execution.scope,
                  "equity": float(self.client.account_info().equity) if self.execution.mode == TradingMode.LIVE else self.paper_equity(),
                  "planned_open_risk": exposure,
                  "quotes":self.quote_statuses,
                  "news":self.news_filter.status(),
                  "position_management": self.repository.get_state(self.execution.scope + ":position_management"),
                  "risk_state": asdict(self.risk.state),
                  "last_decision": self.repository.get_state(self.execution.scope + ":decision", "Waiting for data")}
        self.repository.set_state("heartbeat", status)
        logger.info("heartbeat {}", json.dumps(status))

    def record_decision(self, decision, stage, reason=None, code=None, **extra):
        result = {**decision, **extra, "stage": stage, "reason_code": code or reason_code(reason),
                  "updated_at": datetime.now(timezone.utc).isoformat()}
        if reason:
            result.update(direction="HOLD", reason=reason)
        analysis = self.repository.get_state("analysis:" + result["symbol"], {})
        self.repository.record_decisions(self.execution.scope,[result],states={
            self.execution.scope+":decision":result,"analysis:"+result["symbol"]:{**analysis,**result}})
        if stage in {"risk","execution"}:
            waiting = result["reason_code"] if result["reason_code"] in {"LOSS_PAUSE","LOSS_LIMIT","DAILY_COUNT","KILL_SWITCH"} else None
            self.track_wait(result["symbol"],"risk",waiting)
        return result

    def track_wait(self, symbol, category, code=None):
        key = f"{self.execution.scope}:wait:{category}:{symbol}"
        state = self.repository.get_state(key,{"totals_seconds":{}})
        now = self.risk.clock()
        previous = state.get("reason_code")
        if previous is None and code is None:
            return
        if previous and state.get("last_observed"):
            delta = (now-datetime.fromisoformat(state["last_observed"])).total_seconds()
            if 0<=delta<=float(self.cfg.get("trading.poll_seconds",2))*2:
                state["totals_seconds"][previous] = state["totals_seconds"].get(previous,0)+delta
        if previous != code:
            state["started_at"] = now.isoformat() if code else None
        state.update(symbol=symbol,category=category,reason_code=code,last_observed=now.isoformat())
        self.repository.set_state(key,state)

    def record_unavailable(self, symbol, reason, code, **extra):
        self.track_wait(symbol,"availability",code)
        decision={"symbol":symbol,"direction":"HOLD","reason":reason,"reason_code":code,
            "stage":"availability","updated_at":self.risk.clock().isoformat(),**extra}
        self.repository.set_state(self.execution.scope+":decision",decision)
        analysis=self.repository.get_state("analysis:"+symbol,{})
        self.repository.set_state("analysis:"+symbol,{**analysis,**decision})

    def process_symbol(self, logical_symbol: str, submit=True):
        if self.execution.kill_switch_path.exists():
            logger.warning("Kill switch active")
            self.record_unavailable(self.resolved.get(logical_symbol,logical_symbol),"Kill switch active","KILL_SWITCH")
            return None
        if logical_symbol not in self.resolved:
            self.resolved[logical_symbol] = self.resolver.resolve(logical_symbol)
        symbol = self.resolved[logical_symbol]
        quote_status = self.feed.tick_status(symbol)
        self.quote_statuses[symbol] = quote_status
        self.feed.log_tick_status(quote_status)
        if not quote_status["fresh"]:
            self.record_unavailable(symbol,quote_status["reason"],quote_status["status"],quote_status=quote_status["status"])
            return None
        frames = self.load_frames(symbol)
        self.track_wait(symbol,"availability")
        regime = detect_regime(frames["trend"], self.cfg.get("strategy.filters", {}))
        structure = analyze_structure(frames["setup"])
        volume = analyze_volume(frames["setup"], self.cfg.get("strategy.indicators", {}))
        news_ok, news_reason, news_score = self.news_filter.evaluate(logical_symbol)
        historical = list(frames["entry"].get("spread", []))[-100:]
        spread_status=self.feed.spread_status(symbol,historical,float(self.cfg.get("strategy.filters.max_spread_multiplier",2)),
            float(self.risk.policy(symbol).get("max_spread_points",40)))
        spread,spread_ok=spread_status["points"],spread_status["allowed"]
        context = pricing_context(self.client,symbol,self.risk.policy(symbol))
        signal = self.signal_engine.generate(symbol, frames, regime, structure, volume, news_ok, news_reason, news_score, spread,context)
        if not news_ok and signal.direction!=Direction.HOLD:
            signal.direction=Direction.HOLD
            signal.stop_loss=signal.take_profit=signal.risk_reward=None
            signal.reasons.append(news_reason)
            signal.meta.setdefault("reason_codes",[]).insert(0,"NEWS")
        if not spread_ok:
            signal.direction = Direction.HOLD
            signal.stop_loss = signal.take_profit = signal.risk_reward = None
            signal.reasons.append("Abnormal spread; entry suspended")
            signal.meta.setdefault("reason_codes",[]).insert(0,"SPREAD")
        if signal.direction != Direction.HOLD and not self.news_filter.direction_allowed(logical_symbol, signal.direction.value):
            signal.direction = Direction.HOLD
            signal.stop_loss = signal.take_profit = signal.risk_reward = None
            signal.reasons.append("Configured economic surprise conflicts with technical direction")
            signal.meta.setdefault("reason_codes",[]).insert(0,"NEWS")
        new_signal = self.repository.save_signal(signal)
        candle = signal.candle_time or frames["entry"].time.iloc[-1]
        candle_datetime = candle.to_pydatetime() if hasattr(candle,"to_pydatetime") else candle
        seconds = {"M1":60,"M5":300,"M15":900,"M30":1800,"H1":3600,"H4":14400,"D1":86400}[self.cfg.get("strategy.timeframes.entry","M5")]
        decision = {"symbol": symbol, "direction": signal.direction.value, "score": signal.confidence, "candle": str(candle), "strategy":signal.strategy,
                    "evaluation_id":evaluation_id(symbol, candle, signal.strategy, self.revision), "config_revision":self.revision,
                    "regime":getattr(signal.market_regime,"value","UNCERTAIN"),
                    "session":", ".join(active_sessions(candle_datetime + timedelta(seconds=seconds))) or "closed",
                    "news":{"allowed":news_ok,"reason":news_reason},
                    "spread":spread_status,
                    "selection":signal.meta.get("selection"),"trade_plan":signal.meta.get("trade_plan"), "reasons": signal.reasons}
        signal.meta["runtime_decision"] = decision
        assessments=[]
        for assessment in (signal.meta.get("selection") or {}).get("assessments", []):
            method = assessment["strategy"]
            assessments.append({**decision, "strategy":method,
                "evaluation_id":evaluation_id(symbol,candle,method,self.revision), "stage":"setup",
                "reason_code":"SETUP" if assessment["eligible"] else reason_code("; ".join(assessment["reasons"])),
                "assessment":assessment})
        self.repository.record_decisions(self.execution.scope,assessments)
        if signal.direction == Direction.HOLD:
            blocked = signal.meta.get("reason_codes", [])
            self.record_decision(decision, "analysis", "; ".join(signal.reasons), blocked[0] if blocked else "NO_SETUP",indicators=signal.meta.get("indicators"))
        else:
            self.record_decision(decision, "analysis", code="CANDIDATE",indicators=signal.meta.get("indicators"))
        if new_signal:
            logger.info("signal {}", json.dumps({k:v for k,v in decision.items() if k!="selection"}, ensure_ascii=False))
        if signal.direction == Direction.HOLD:
            return signal
        if not news_ok:
            self.record_decision(decision, "news", news_reason, "NEWS")
            return signal
        if submit:
            self.submit_signal(logical_symbol, signal)
        return signal

    def correlation_matrix(self, exposure):
        import pandas as pd
        closes = {symbol: df.set_index("time").close for (symbol, role), df in self.frames_cache.items() if role == "trend"}
        for symbol, _ in exposure:
            if symbol in closes:
                continue
            try:
                frame = self.feed.bars(symbol, self.cfg.get("strategy.timeframes.trend", "H1"),
                    int(self.cfg.get("trading.correlation_lookback",250)) + 1)
                if not frame.empty:
                    closes[symbol] = frame.set_index("time").close
            except (ValueError, MT5Error, TimeoutError, ConnectionError):
                pass # Explicit mapped-factor fallback handles absent broker history.
        return self.correlation.matrix(closes, int(self.cfg.get("trading.correlation_lookback",250))) if closes else pd.DataFrame()

    def portfolio_check(self, req, positions, orders, simulated):
        exposure = [(p.symbol, "BUY" if int(p.type) in (0,2,4,6) else "SELL") for p in [*positions, *orders]]
        exposure += [(p["symbol"],p["direction"]) for p in simulated]
        allowed, reason = self.correlation.can_open(req.symbol, exposure, self.correlation_matrix(exposure), req.direction)
        if not allowed:
            raise ValueError(reason)

    def before_send(self, req):
        from core.symbol_policy import symbol_metadata
        logical, _ = symbol_metadata(req.symbol, self.cfg.symbols)
        ok, reason, _ = self.news_filter.evaluate(logical or req.symbol)
        if not ok or not self.news_filter.direction_allowed(logical or req.symbol, req.direction.value):
            raise ValueError("News changed before order submission: " + reason)
        frame = self.frames_cache.get((req.symbol, "entry"))
        history = list(frame.get("spread", []))[-100:] if frame is not None else []
        spread=self.feed.spread_status(req.symbol,history,float(self.cfg.get("strategy.filters.max_spread_multiplier",2)),
            float(self.risk.policy(req.symbol).get("max_spread_points",40)))
        if not spread["allowed"]:
            raise ValueError("Abnormal spread before order submission")

    def submit_signal(self, logical_symbol, signal):
        decision = signal.meta["runtime_decision"]
        symbol = signal.symbol
        self.risk.mode = self.execution.mode.value
        cooldown = self.repository.get_state(self.execution.scope + ":defensive_exit:" + symbol)
        if cooldown and datetime.now(timezone.utc) < datetime.fromisoformat(cooldown["until"]):
            self.record_decision(decision, "risk", "Cooldown after defensive exit", cooldown=cooldown)
            return signal
        account = self.client.account_info()
        if account is None:
            raise ValueError("Account unavailable")
        equity = float(account.equity) if self.execution.mode == TradingMode.LIVE else self.paper_equity()
        exposure, positions, orders = self.risk.account_exposure()
        simulated = self.execution.paper_positions() if self.execution.mode == TradingMode.PAPER else []
        exposure += sum(p["risk"] for p in simulated)
        if any(p.symbol==symbol for p in [*positions,*orders]) or any(p["symbol"]==symbol for p in simulated):
            self.record_decision(decision,"risk","Existing symbol exposure; duplicate entry blocked","DUPLICATE")
            return signal
        allowed, reason = self.risk.can_trade(equity, exposure / equity)
        if not allowed:
            logger.warning("{} rejected: {}", symbol, reason)
            self.record_decision(decision, "risk", reason)
            return signal
        info = self.client.symbol_info(symbol)
        if info is None:
            self.record_decision(decision, "risk", "Symbol specification unavailable")
            return signal
        lot = self.risk.calculate_volume(symbol, signal.entry_price, signal.stop_loss, equity, info, exposure, signal.direction)
        if lot <= 0:
            logger.warning("{} rejected: calculated volume below broker minimum", symbol)
            self.record_decision(decision, "risk", "Calculated volume below broker minimum", "MIN_LOT")
            return signal
        tick = self.client.symbol_info_tick(symbol)
        price = float(tick.ask if signal.direction == Direction.BUY else tick.bid)
        req = OrderRequest(symbol, signal.direction, lot, price, signal.stop_loss, signal.take_profit, int(self.risk.policy(symbol).get("max_deviation_points", 20)), magic=int(self.cfg.get("trading.magic", 260912)), comment=signal.signal_id or "quant-bot", signal_id=signal.signal_id)
        req.minimum_net_rr = signal.meta.get("trade_plan",{}).get("minimum_net_rr")
        if not self.running:
            return signal
        # A new snapshot may have arrived while sizing/preparing this entry.
        latest_ok, latest_reason, _ = self.news_filter.evaluate(logical_symbol)
        if not latest_ok or not self.news_filter.direction_allowed(logical_symbol, signal.direction.value):
            self.record_decision(decision,"news","News changed before order submission", "NEWS",
                news={"allowed":False,"reason":latest_reason})
            return signal
        result = self.execution.execute(req)
        if not result.message.startswith("Duplicate signal"):
            self.repository.save_execution(signal, lot, result)
        if result.success:
            self.risk.state.trades_today += 1
        self.persist_risk()
        self.record_decision(decision,"execution", None if result.success else result.message,
            "FILLED" if result.success else reason_code(result.message), execution=result.message,
            risk=result.planned_risk,volume=result.filled_volume)
        logger.info("{} {} confidence={:.2f} result={}", symbol, signal.direction.value, signal.confidence, result.message)
        return signal

    def load_frames(self, symbol):
        frames = {}
        for role, tf in self.cfg.get("strategy.timeframes", {}).items():
            df = self.feed.bars(symbol, tf, int(self.cfg.get("trading.default_bars", 700)),allow_session_gaps=role!="entry")
            if df.empty:
                raise ValueError(f"{symbol}: no {tf} data")
            cached = self.frames_cache.get((symbol, role))
            if cached is not None and cached.time.iloc[-1] == df.time.iloc[-1] and cached.close.iloc[-1] == df.close.iloc[-1]:
                frames[role] = cached
            else:
                frames[role] = add_indicators(df, self.cfg.get("strategy.indicators", {}))
                self.frames_cache[(symbol, role)] = frames[role]
        return frames

    def position_decision(self, symbol, direction, entry, current, sl, meta, frames):
        # The price-based safety exit must work even if chart data is unavailable.
        decision = self.position_manager.decide(direction, entry, current, sl, meta["initial_risk"])
        if decision.close:
            return decision
        if symbol not in frames:
            try:
                frames[symbol] = self.load_frames(symbol)
            except (MT5Error, ValueError, TimeoutError, ConnectionError) as exc:
                logger.warning("{} position analysis unavailable: {}", symbol, exc)
                frames[symbol] = {}
        chart = frames[symbol]
        reason = self.position_manager.invalidation_reason(direction, chart, meta.get("created"))
        atr = float(chart["entry"].atr.iloc[-1]) if "entry" in chart and "atr" in chart["entry"] else None
        return self.position_manager.decide(direction, entry, current, sl, meta["initial_risk"], atr,
            meta.get("partial_done", False) or meta.get("partial_state") is not None, invalidation_reason=reason)

    def record_position_exit(self, symbol, ticket, reason):
        self.repository.set_state(self.execution.scope + ":position_management", {
            "symbol": symbol, "ticket": int(ticket), "action": "CLOSE", "reason": reason,
            "time": datetime.now(timezone.utc).isoformat()})
        if reason not in {"Stop loss", "Take profit"}:
            until = datetime.now(timezone.utc) + timedelta(
                seconds=float(self.cfg.get("position_management.defensive_exit_cooldown_seconds", 900)))
            self.repository.set_state(self.execution.scope + ":defensive_exit:" + symbol, {
                "until": until.isoformat(), "reason": reason, "ticket": int(ticket)})
        logger.warning("Position exit ticket={} symbol={} reason={}", ticket, symbol, reason)

    def manage_positions(self):
        frames = {}
        if self.execution.mode == TradingMode.PAPER:
            for p in self.execution.paper_positions():
                if not self.feed.is_tick_fresh(p["symbol"]):
                    continue
                tick = self.client.symbol_info_tick(p["symbol"])
                buy = p["direction"] == "BUY"
                current = float(tick.bid if buy else tick.ask)
                hit_sl = current <= p["sl"] if buy else current >= p["sl"]
                hit_tp = current >= p["tp"] if buy else current <= p["tp"]
                decision = None if hit_sl or hit_tp else self.position_decision(
                    p["symbol"], p["direction"], p["price"], current, p["sl"], p, frames)
                if hit_sl or hit_tp or decision.close:
                    reason = "Stop loss" if hit_sl else "Take profit" if hit_tp else decision.reason
                    exit_price = p["tp"] if hit_tp and not hit_sl else current
                    slip = float(self.risk.policy(p["symbol"]).get("slippage_points", 0)) * self.client.symbol_info(p["symbol"]).point
                    exit_price += -slip if buy else slip
                    profit = self.client.order_calc_profit(0 if buy else 1, p["symbol"], p["volume"], p["price"], exit_price)
                    if profit is None or not math.isfinite(profit):
                        raise ValueError("Paper close calculation unavailable")
                    profit -= float(self.risk.policy(p["symbol"]).get("commission_per_lot", 0)) * p["volume"]
                    state = self.risk.closed_state(profit + p.get("partial_realized", 0))
                    if self.repository.settle_paper(p["key"], self.execution.scope, p["ticket"], exit_price, profit, reason=reason, risk_state=state):
                        self.risk.state = state
                    self.record_position_exit(p["symbol"], p["ticket"], reason)
                    self.persist_risk()
                    continue
                if decision.partial_close_fraction:
                    info = self.client.symbol_info(p["symbol"])
                    volume = self.risk._floor_step(p["volume"] * decision.partial_close_fraction, info.volume_step)
                    if volume >= info.volume_min and p["volume"] - volume >= info.volume_min - 1e-9:
                        slip = float(self.risk.policy(p["symbol"]).get("slippage_points",0))*info.point
                        exit_price = current + (-slip if buy else slip)
                        profit = self.client.order_calc_profit(0 if buy else 1, p["symbol"], volume, p["price"], exit_price)
                        if profit is None or not math.isfinite(profit):
                            raise ValueError("Paper partial-close calculation unavailable")
                        profit -= float(self.risk.policy(p["symbol"]).get("commission_per_lot", 0)) * volume
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
        if self.execution.mode != TradingMode.LIVE or not self.execution.enable_live:
            return
        positions = self.client.positions_get()
        if positions is None:
            raise ValueError("Position management unavailable")
        journal = [dict(json.loads(a.payload), key=a.key) for a in self.repository.attempts(self.execution.scope) if a.status in {"OPEN", "PARTIAL", "PROTECTION_FAILURE"}]
        for p in positions:
            meta = next((a for a in journal if a.get("position_ticket") == int(p.ticket) and a["magic"] == int(p.magic)), None)
            if meta is None or not self.feed.is_tick_fresh(p.symbol):
                continue
            if not math.isfinite(float(p.sl)) or float(p.sl) <= 0:
                self.record_position_exit(p.symbol, p.ticket, "Bot position without valid SL")
                self.execution.manage_close(p, meta["key"], "Bot position without valid SL")
                continue
            info, tick = self.client.symbol_info(p.symbol), self.client.symbol_info_tick(p.symbol)
            current = float(tick.bid if int(p.type) == 0 else tick.ask)
            if meta.get("close_reason"):
                self.execution.manage_close(p, meta["key"], meta["close_reason"])
                continue
            decision = self.position_decision(p.symbol, "BUY" if int(p.type) == 0 else "SELL",
                float(p.price_open), current, float(p.sl), meta, frames)
            if decision.close:
                self.record_position_exit(p.symbol, p.ticket, decision.reason)
                self.execution.manage_close(p, meta["key"], decision.reason)
                continue
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
            equity += pnl - float(self.risk.policy(p["symbol"]).get("commission_per_lot", 0)) * p["volume"]
        return equity
