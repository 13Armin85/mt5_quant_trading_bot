from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.data_feed import DataFeed
from core.mt5_client import MT5Error
from utils.types import Direction, ExecutionResult, TradingMode


def filling_mode(info):
    # SYMBOL_FILLING_* are flags; ORDER_FILLING_* are enum values.
    flags = int(info.filling_mode)
    if flags & 1:
        return 0
    if flags & 2:
        return 1
    if int(info.trade_exemode) != 2:
        return 2
    raise ValueError("Broker has no supported filling policy")


class ExecutionEngine:
    def __init__(self, client, mode, enable_live, kill_switch_path, risk=None, repository=None, cfg=None, scope="default"):
        self.client, self.mode, self.enable_live = client, mode, enable_live
        self.kill_switch_path = Path(kill_switch_path)
        self.risk, self.repo, self.cfg, self.scope = risk, repository, cfg or {}, scope
        self.paper_tickets = 1_000_000
        self.seen = set()

    def key(self, req):
        return f"{self.scope}:{req.signal_id or req.comment}"

    def preflight(self, req):
        if self.kill_switch_path.exists():
            return False, "Kill switch active"
        if self.mode == TradingMode.LIVE and not self.enable_live:
            return False, "ENABLE_LIVE_TRADING is false"
        if req.direction not in (Direction.BUY, Direction.SELL):
            return False, "Invalid order direction"
        if not all(math.isfinite(float(v)) and float(v) > 0 for v in (req.volume, req.price, req.sl, req.tp)):
            return False, "Invalid price/volume/SL/TP"
        if not (req.sl < req.price < req.tp if req.direction == Direction.BUY else req.tp < req.price < req.sl):
            return False, "Invalid stop/target direction"
        if self.mode == TradingMode.LIVE and (self.repo is None or self.risk is None or not req.signal_id):
            return False, "LIVE requires persistent journal, risk manager and signal ID"
        if req.minimum_net_rr is not None and (not math.isfinite(req.minimum_net_rr) or req.minimum_net_rr<=0):
            return False,"Invalid minimum net reward/risk"
        return True, "OK"

    def _prepare(self, req):
        policy = self.risk.policy(req.symbol)
        if not self.client.health_check():
            raise ValueError("MT5 connection unhealthy")
        if not DataFeed(self.client, int(self.cfg.get("max_tick_age_seconds", 15)),self.risk.symbols).is_tick_fresh(req.symbol):
            raise ValueError("Stale or invalid tick")
        info, tick, account = self.client.symbol_info(req.symbol), self.client.symbol_info_tick(req.symbol), self.client.account_info()
        if info is None or account is None:
            raise ValueError("Missing account/symbol")
        if self.mode == TradingMode.LIVE:
            terminal = self.client.terminal_info()
            if not terminal or not terminal.trade_allowed or getattr(terminal, "tradeapi_disabled", True) or not account.trade_allowed or not account.trade_expert:
                raise ValueError("Account/terminal algorithmic trading disabled")
        mode = int(info.trade_mode)
        if mode not in (1, 2, 4) or (mode == 1 and req.direction != Direction.BUY) or (mode == 2 and req.direction != Direction.SELL):
            raise ValueError("Symbol does not permit this entry")
        if not math.isfinite(info.point) or info.point <= 0 or not math.isfinite(info.trade_tick_size) or info.trade_tick_size <= 0:
            raise ValueError("Invalid broker point/tick size")
        if (tick.ask - tick.bid) / info.point > float(policy.get("max_spread_points", 40)):
            raise ValueError("Spread exceeds limit")
        req.price = float(tick.ask if req.direction == Direction.BUY else tick.bid)
        if self.mode == TradingMode.PAPER:
            req.price += (1 if req.direction == Direction.BUY else -1) * float(policy.get("slippage_points", 0)) * info.point
        unit = float(info.trade_tick_size)
        req.sl = round(round(req.sl / unit) * unit, int(info.digits))
        req.tp = round(round(req.tp / unit) * unit, int(info.digits))
        ok, reason = self.preflight(req)
        if not ok:
            raise ValueError(reason)
        liquidation = float(tick.bid if req.direction == Direction.BUY else tick.ask)
        minimum = max(int(info.trade_stops_level), int(info.trade_freeze_level)) * info.point
        if min(abs(liquidation - req.sl), abs(liquidation - req.tp)) < minimum or not (req.sl < liquidation < req.tp if req.direction == Direction.BUY else req.tp < liquidation < req.sl):
            raise ValueError("Broker stops/freeze distance violated")
        entry_buffer = float(policy.get("slippage_points",0))*float(info.point) if self.mode==TradingMode.LIVE else 0
        worst_entry = req.price + (1 if req.direction==Direction.BUY else -1)*entry_buffer
        economics = self.risk.reward_risk(req.symbol,req.direction,worst_entry,req.sl,req.tp,info)
        if economics["net_rr"]+1e-8<max(float(self.cfg.get("min_rr",1.5)),req.minimum_net_rr or 0):
            raise ValueError("Final net reward/risk below minimum after costs and current quote")
        exposure, positions, orders = self.risk.account_exposure()
        simulated = self.paper_positions() if self.mode == TradingMode.PAPER else []
        if hasattr(self, "portfolio_check"):
            self.portfolio_check(req, positions, orders, simulated)
        if len(positions) + len(orders) >= int(self.cfg.get("max_positions", 3)):
            raise ValueError("Maximum positions reached")
        if any(p.symbol == req.symbol for p in [*positions, *orders]):
            raise ValueError("Existing symbol exposure (netting/hedging entry blocked)")
        if self.mode == TradingMode.PAPER:
            simulated = self.paper_positions()
            exposure += sum(p["risk"] for p in simulated)
            if len(positions) + len(orders) + len(simulated) >= int(self.cfg.get("max_positions", 3)):
                raise ValueError("Maximum positions reached")
            if any(p["symbol"] == req.symbol for p in simulated):
                raise ValueError("Existing paper symbol exposure")
            equity = float(self.cfg.get("paper_starting_balance", 10000)) + self.repo.get_state(self.scope + ":paper_pnl", 0)
            for p in simulated:
                quote = self.client.symbol_info_tick(p["symbol"])
                if quote is None:
                    raise ValueError("Paper mark-to-market unavailable")
                mark = quote.bid if p["direction"] == "BUY" else quote.ask
                pnl = self.client.order_calc_profit(0 if p["direction"] == "BUY" else 1, p["symbol"], p["volume"], p["price"], mark)
                if pnl is None:
                    raise ValueError("Paper equity calculation unavailable")
                equity += pnl - self.risk.commission_per_lot(p["symbol"], p["price"]) * p["volume"]
        else:
            equity = float(account.equity)
        allowed, reason = self.risk.can_trade(equity, exposure / equity)
        if not allowed:
            raise ValueError(reason)
        req.volume = min(req.volume, self.risk.calculate_volume(req.symbol, worst_entry, req.sl, equity, info, exposure, req.direction))
        req.volume = self.risk._floor_step(req.volume, float(info.volume_step))
        if req.volume < info.volume_min:
            raise ValueError("Remaining risk budget below broker minimum")
        planned = self.risk.loss_per_lot(req.symbol, worst_entry, req.sl, info, req.direction) * req.volume
        margin = self.client.order_calc_margin(0 if req.direction == Direction.BUY else 1, req.symbol, req.volume, req.price)
        free_margin = float(account.margin_free)
        if self.mode == TradingMode.PAPER:
            occupied = 0.0
            for p in self.paper_positions():
                used = self.client.order_calc_margin(0 if p["direction"] == "BUY" else 1, p["symbol"], p["volume"], p["price"])
                if used is None or not math.isfinite(used) or used < 0:
                    raise ValueError("Paper margin calculation unavailable")
                occupied += used
            free_margin = min(free_margin, equity - occupied)
        if margin is None or not math.isfinite(margin) or margin < 0 or margin > free_margin:
            raise ValueError("Insufficient or unknown free margin")
        if planned > min(self.risk.risk_amount(equity), equity * .01 - exposure) + 1e-8:
            raise ValueError("Final risk exceeds budget")
        return info, planned

    def execute(self, req):
        ok, reason = self.preflight(req)
        if not ok:
            return ExecutionResult(False, None, None, reason)
        key = self.key(req)
        if key in self.seen:
            return ExecutionResult(False, None, None, "Duplicate signal")
        if self.repo and any(a.status in {"SUBMITTING", "UNKNOWN", "PROTECTION_FAILURE"}
                or json.loads(a.payload).get("partial_state") in {"SUBMITTING", "UNKNOWN"}
                or json.loads(a.payload).get("close_reason") for a in self.repo.attempts(self.scope)
                if a.status not in {"CLOSED", "PAPER_CLOSED", "REJECTED"}):
            return ExecutionResult(False, None, None, "Unresolved broker execution; entries suspended")
        try:
            planned, info = None, None
            if self.risk is not None:
                info, planned = self._prepare(req)
            payload = {"symbol": req.symbol, "direction": req.direction.value, "profile": req.profile,
                       "volume": req.volume,
                       "price": req.price, "sl": req.sl, "tp": req.tp, "risk": planned,
                       "magic": req.magic, "comment": (req.signal_id or req.comment)[:31],
                       "initial_risk": abs(req.price - req.sl), "created": datetime.now(timezone.utc).isoformat()}
            payload["requested_volume"] = req.volume
            payload["minimum_net_rr"] = max(float(self.cfg.get("min_rr",1.5)),req.minimum_net_rr or 0)
            if self.risk is not None:
                payload["reward_risk"] = self.risk.reward_risk(req.symbol,req.direction,req.price,req.sl,req.tp,info)
            policy = filling_mode(info) if self.mode == TradingMode.LIVE else None
        except (ValueError, ArithmeticError) as exc:
            return ExecutionResult(False, None, None, str(exc))
        if self.repo and not self.repo.claim_execution(key, payload):
            return ExecutionResult(False, None, None, "Duplicate signal (persistent journal)")
        self.seen.add(key)
        if self.mode != TradingMode.LIVE:
            self.paper_tickets += 1
            ticket = int(datetime.now(timezone.utc).timestamp() * 1_000_000) if self.repo else self.paper_tickets
            payload["ticket"] = ticket
            if self.repo:
                self.repo.update_attempt(key, "PAPER_OPEN", payload)
            return ExecutionResult(True, ticket, 0, f"{self.mode.value} simulated fill", req.price,
                                   status="PAPER_OPEN", filled_volume=req.volume, planned_risk=planned)
        request = {"action": 1, "symbol": req.symbol, "volume": req.volume,
                   "type": 0 if req.direction == Direction.BUY else 1, "price": req.price,
                   "sl": req.sl, "tp": req.tp, "deviation": req.deviation, "magic": req.magic,
                   "comment": payload["comment"], "type_time": 0, "type_filling": policy}
        try:
            check = self.client.order_check(request)
        except (MT5Error, TimeoutError, ConnectionError):
            self.reject_before_send(key,payload)
            raise
        if check is None or int(check.retcode) not in (0, 10009):
            self.reject_before_send(key,payload)
            return ExecutionResult(False, None, getattr(check, "retcode", None), "Broker order_check rejected")
        if self.kill_switch_path.exists():
            self.reject_before_send(key,payload)
            return ExecutionResult(False, None, None, "Kill switch active")
        try:
            if hasattr(self, "before_send"):
                self.before_send(req)
            fresh_tick = self.client.symbol_info_tick(req.symbol)
            if fresh_tick is None:
                raise ValueError("Quote unavailable after order_check")
            fresh_price = float(fresh_tick.ask if req.direction == Direction.BUY else fresh_tick.bid)
            if abs(fresh_price - req.price) > req.deviation * float(info.point) + 1e-12:
                raise ValueError("Quote changed beyond permitted deviation during order_check")
            final = replace(req)
            final_info, final_risk = self._prepare(final)
            if final.sl != req.sl or final.tp != req.tp or final.volume > req.volume + 1e-8:
                raise ValueError("Stops or volume changed during order_check")
            if abs(final.price - req.price) > req.deviation * float(final_info.point) + 1e-12:
                raise ValueError("Quote changed beyond permitted deviation during order_check")
            if final_risk > planned + 1e-8:
                # A fresh quote can increase loss per lot. Keep the original risk
                # ceiling by reducing volume to a broker-valid step, if possible.
                per_lot = self.risk.loss_per_lot(req.symbol,
                    final.price + (1 if req.direction == Direction.BUY else -1) *
                    float(self.risk.policy(req.symbol).get("slippage_points", 0)) * final_info.point,
                    final.sl, final_info, req.direction)
                final.volume = self.risk._floor_step(min(final.volume, planned / per_lot), float(final_info.volume_step))
                if final.volume < final_info.volume_min:
                    raise ValueError("Quote change leaves volume below broker minimum")
                final_info, final_risk = self._prepare(final)
            if final_risk > planned + 1e-8:
                raise ValueError("Final risk exceeds checked risk after quote change")
            changed = (final.price, final.volume, final.sl, final.tp) != (req.price, req.volume, req.sl, req.tp)
            if changed:
                request.update(price=final.price, volume=final.volume, sl=final.sl, tp=final.tp)
                second = self.client.order_check(request)
                if second is None or int(second.retcode) not in (0, 10009):
                    raise ValueError("Broker order_check rejected repriced order")
                checked = replace(final)
                _, checked_risk = self._prepare(checked)
                if ((checked.price, checked.volume, checked.sl, checked.tp) !=
                    (final.price, final.volume, final.sl, final.tp) or checked_risk > planned + 1e-8):
                    raise ValueError("Quote/equity/exposure changed during repriced order_check")
            payload.update(price=final.price, volume=final.volume, risk=final_risk,
                requested_volume=final.volume, initial_risk=abs(final.price - final.sl))
            payload["reward_risk"] = self.risk.reward_risk(req.symbol, req.direction,
                final.price, final.sl, final.tp, final_info)
            self.repo.update_attempt(key, "SUBMITTING", payload)
            planned = final_risk
        except (ValueError, MT5Error, TimeoutError, ConnectionError) as exc:
            self.reject_before_send(key,payload)
            return ExecutionResult(False, None, None, str(exc))
        try:
            result = self.client.order_send(request)
        except (MT5Error, TimeoutError, ConnectionError):
            result = None
        if result is None or int(result.retcode) in (10008, 10012, 10031):
            self.repo.update_attempt(key, "UNKNOWN")
            self.reconcile()
            return ExecutionResult(False, None, getattr(result, "retcode", None), "Ambiguous response; reconciled journal, no resend", status="UNKNOWN")
        code = int(result.retcode)
        if code not in (10009, 10010):
            self.repo.update_attempt(key, "REJECTED")
            return ExecutionResult(False, None, code, f"Broker rejected order ({code})")
        payload.update(ticket=int(result.order or result.deal), price=float(result.price), volume=float(result.volume))
        protected = self.verify_fill(payload, getattr(result, "deal", None), key)
        status = ("PARTIAL" if code == 10010 else "OPEN") if protected else "PROTECTION_FAILURE"
        self.repo.update_attempt(key, status, payload)
        return ExecutionResult(protected, payload["ticket"], code, status, payload["price"], result,
                               status, payload["volume"], planned)

    def reject_before_send(self, key, payload):
        # Only a provably unsent request may be reconsidered on the same candle.
        # SUBMITTING/UNKNOWN and every actual send retain their permanent claim.
        self.repo.update_attempt(key,"REJECTED",{**payload,"retryable_before_send":True})
        self.seen.discard(key)

    def verify_fill(self, payload, deal=None, key=None):
        positions = self.client.positions_get(symbol=payload["symbol"])
        if positions is None:
            return False
        matches = [p for p in positions if int(p.magic) == payload["magic"] and p.comment == payload["comment"]]
        if not matches and deal:
            deals = self.client.history_deals_get(ticket=int(deal))
            identifiers = {int(d.position_id) for d in deals or ()}
            matches = [p for p in positions if int(getattr(p, "identifier", p.ticket)) in identifiers and int(p.magic) == payload["magic"]]
        if len(matches) != 1:
            return False
        p = matches[0]
        payload["position_ticket"] = int(p.ticket)
        info = self.client.symbol_info(p.symbol)
        valid = (info is not None and math.isfinite(p.sl) and p.sl > 0
                 and abs(float(p.sl) - payload["sl"]) <= float(info.trade_tick_size) / 2
                 and math.isfinite(p.tp) and abs(float(p.tp) - payload["tp"]) <= float(info.trade_tick_size) / 2
                 and abs(float(p.volume) - payload["volume"]) < 1e-8
                 and 0 < float(p.volume) <= payload["requested_volume"] + 1e-8
                 and abs(float(p.price_open) - payload["price"]) <= float(info.trade_tick_size))
        if valid:
            actual_risk = self.risk.loss_per_lot(p.symbol, float(p.price_open), float(p.sl), info, payload["direction"]) * float(p.volume)
            valid = actual_risk <= payload["risk"] + 1e-7
            economics = self.risk.reward_risk(p.symbol,payload["direction"],float(p.price_open),float(p.sl),float(p.tp),info)
            valid = valid and economics["net_rr"]+1e-8>=payload.get("minimum_net_rr",float(self.cfg.get("min_rr",1.5)))
        if valid:
            return True
        if key and self.repo:
            self.repo.update_attempt(key,"PROTECTION_FAILURE",payload)
            self.manage_close(p,key,"Fill protection verification failed")
            saved = next(a for a in self.repo.attempts(self.scope) if a.key==key)
            payload.update(json.loads(saved.payload))
        else:
            self.close_position(p, payload["magic"])
        return False

    def close_position(self, p, magic):
        info, tick = self.client.symbol_info(p.symbol), self.client.symbol_info_tick(p.symbol)
        if info is None or tick is None:
            return False
        request = {"action": 1, "position": int(p.ticket), "symbol": p.symbol, "volume": float(p.volume),
                   "type": 1 if int(p.type) == 0 else 0, "price": float(tick.bid if int(p.type) == 0 else tick.ask),
                   "magic": magic, "deviation": int(self.cfg.get("max_deviation_points", 20)),
                   "comment": "emergency-protection", "type_time": 0, "type_filling": filling_mode(info)}
        result = self.client.order_send(request)
        return result is not None and int(result.retcode) == 10009

    def paper_positions(self):
        if not self.repo:
            return []
        return [dict(json.loads(a.payload), key=a.key) for a in self.repo.attempts(self.scope) if a.status == "PAPER_OPEN"]

    def manage_close(self, position, key, reason):
        """Persist exit intent before sending; never resend an ambiguous close.

        Rejected requests and confirmed partial fills can retry on the next fresh
        position snapshot. Unknown outcomes retain broker SL/TP and block entries
        until broker history confirms closure or the journal is resolved.
        """
        if self.mode != TradingMode.LIVE or not self.enable_live or not self.repo:
            return False
        attempt = next((a for a in self.repo.attempts(self.scope) if a.key == key), None)
        if attempt is None or attempt.status not in {"OPEN", "PARTIAL", "PROTECTION_FAILURE"}:
            return False
        meta = json.loads(attempt.payload)
        if (meta.get("position_ticket") != int(position.ticket) or meta["magic"] != int(position.magic)
                or meta["symbol"] != position.symbol):
            return False
        if meta.get("close_state") in {"SUBMITTING", "UNKNOWN", "DONE"}:
            return False
        meta["close_reason"] = reason
        self.repo.update_attempt(key, attempt.status, meta)
        if meta.get("partial_state") in {"SUBMITTING", "UNKNOWN"}:
            return False
        if not DataFeed(self.client, int(self.cfg.get("max_tick_age_seconds", 15))).is_tick_fresh(position.symbol):
            return False
        info, tick = self.client.symbol_info(position.symbol), self.client.symbol_info_tick(position.symbol)
        if info is None or tick is None:
            return False
        request = {"action": 1, "position": int(position.ticket), "symbol": position.symbol,
                   "volume": float(position.volume), "type": 1 if int(position.type) == 0 else 0,
                   "price": float(tick.bid if int(position.type) == 0 else tick.ask), "magic": int(position.magic),
                   "deviation": int(self.cfg.get("max_deviation_points", 20)), "comment": "quant-managed-exit",
                   "type_time": 0, "type_filling": filling_mode(info)}
        meta.update(close_reason=reason, close_state="SUBMITTING", close_requested_at=datetime.now(timezone.utc).isoformat())
        self.repo.update_attempt(key, attempt.status, meta)
        try:
            result = self.client.order_send(request)
        except (MT5Error, TimeoutError, ConnectionError):
            result = None
        code = getattr(result, "retcode", None)
        meta["close_retcode"] = code
        meta["close_state"] = ("DONE" if code == 10009 else "PARTIAL" if code == 10010 else
                               "UNKNOWN" if code in (None, 10008, 10012, 10031) else "REJECTED")
        self.repo.update_attempt(key, attempt.status, meta)
        return code == 10009

    def reconcile(self):
        if self.mode != TradingMode.LIVE or not self.repo:
            return
        positions, orders = self.client.positions_get(), self.client.orders_get()
        if positions is None or orders is None:
            raise ValueError("Broker reconciliation unavailable")
        now = datetime.now(timezone.utc)
        for attempt in self.repo.attempts(self.scope):
            if attempt.status not in {"SUBMITTING", "UNKNOWN", "OPEN", "PARTIAL", "PROTECTION_FAILURE"}:
                continue
            p = json.loads(attempt.payload)
            start = datetime.fromisoformat(p["created"]) - timedelta(minutes=1)
            deals = self.client.history_deals_get(start, now)
            history = self.client.history_orders_get(start, now)
            if deals is None or history is None:
                raise ValueError("Broker history unavailable; entries suspended")
            matches = [x for x in [*positions, *orders, *history, *deals]
                       if int(x.magic) == p["magic"] and x.comment == p["comment"]]
            live = [x for x in positions if int(x.magic) == p["magic"] and
                    (x.comment == p["comment"] or int(x.ticket) == p.get("position_ticket"))]
            if live:
                position = live[0]
                stop = float(position.sl)
                side = p["direction"]
                unsafe = (not math.isfinite(stop) or stop <= 0 or (stop < p["sl"] if side == "BUY" else stop > p["sl"]) or float(position.volume) > p.get("requested_volume", p["volume"]) + 1e-8)
                if unsafe and p.get("close_reason"):
                    # The managed exit owns this request, including unknown outcomes.
                    # Keep its state so a later poll cannot submit a duplicate close.
                    self.repo.update_attempt(attempt.key, "PROTECTION_FAILURE", p)
                elif unsafe:
                    p["position_ticket"] = int(position.ticket)
                    self.repo.update_attempt(attempt.key, "PROTECTION_FAILURE",p)
                    self.manage_close(position,attempt.key,"Reconciliation found unsafe stop/volume")
                else:
                    if p.get("partial_state") in {"SUBMITTING", "UNKNOWN"} and float(position.volume) < p["volume"] - 1e-8:
                        p["partial_state"] = "DONE"
                    p.update(position_ticket=int(position.ticket), volume=float(position.volume), ticket=p.get("ticket", int(position.ticket)))
                    self.repo.update_attempt(attempt.key, "OPEN", p)
            elif any(x in orders for x in matches):
                self.repo.update_attempt(attempt.key, "UNKNOWN")
            elif matches:
                identifiers = {int(getattr(x, "position_id", 0)) for x in matches} - {0}
                related = [d for d in deals if int(getattr(d, "position_id", 0)) in identifiers]
                if not any(int(getattr(d, "entry", -1)) in (1, 2, 3) for d in related):
                    self.repo.update_attempt(attempt.key, "UNKNOWN")
                    continue
                pnl = sum(float(d.profit) + float(d.commission) + float(d.swap) + float(getattr(d, "fee", 0)) for d in related)
                state = self.risk.closed_state(pnl)
                if self.repo.settle_live(attempt.key, self.scope, p.get("ticket", 0), float(related[-1].price),
                        pnl, state, reason=p.get("close_reason")):
                    self.risk.state = state
            elif attempt.status in {"SUBMITTING", "UNKNOWN"}:
                self.repo.update_attempt(attempt.key, "UNKNOWN")
