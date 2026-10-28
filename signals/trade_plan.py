"""Causal stop/target geometry and explicit net reward/risk checks.

These configurable rules are research assumptions, not an optimized trading edge.
"""
from dataclasses import dataclass, field, asdict
import math

from analysis.market_structure import _swings
from core.symbol_policy import commission_per_lot
from utils.types import Direction, MarketRegime


@dataclass
class TradePlan:
    accepted: bool
    direction: str
    entry: float | None = None
    sl: float | None = None
    tp: float | None = None
    gross_rr: float | None = None
    net_rr: float | None = None
    target_net_rr: float | None = None
    minimum_net_rr: float = 1.5
    break_even_win_rate: float | None = None
    cost_basis: str = "unverified_price_geometry"
    loss_per_lot: float | None = None
    reward_per_lot: float | None = None
    obstacle: float | None = None
    stop_source: str = "ATR"
    reason: str = ""
    candidates: list[dict] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def _snap(value, tick, upward):
    if not tick:
        return value
    rounded = (math.ceil(value/tick-1e-10) if upward else math.floor(value/tick+1e-10))*tick
    return float(f"{rounded:.12g}")


def net_reward_risk(direction, entry, sl, tp, profit, commission=0.0, exit_slippage=0.0):
    side = getattr(direction,"value",direction)
    if side not in {"BUY","SELL"}:
        raise ValueError("Invalid trade-plan direction")
    if not all(math.isfinite(float(v)) for v in (entry,sl,tp,commission,exit_slippage)) or min(entry,sl,tp)<=0 or min(commission,exit_slippage)<0:
        raise ValueError("Invalid risk/reward price or cost")
    sign = 1 if side=="BUY" else -1
    if sign*(entry-sl)<=0 or sign*(tp-entry)<=0:
        raise ValueError("Invalid risk/reward stop or target side")
    stopped, target = sl-sign*exit_slippage, tp-sign*exit_slippage
    if stopped<=0 or target<=0:
        raise ValueError("Slippage implies nonpositive price")
    loss_pnl, reward_pnl = profit(entry,stopped),profit(entry,target)
    if loss_pnl is None or reward_pnl is None or not math.isfinite(loss_pnl) or not math.isfinite(reward_pnl) or loss_pnl>=0:
        raise ValueError("Account-currency reward/risk calculation failed")
    loss, reward = -float(loss_pnl)+commission,float(reward_pnl)-commission
    return {"loss_per_lot":loss,"reward_per_lot":reward,"net_rr":reward/loss,
            "gross_rr":abs((tp-entry)/(entry-sl)),"break_even_win_rate":loss/(loss+reward) if reward>0 else None}


def pricing_context(client, symbol, trading_cfg):
    info,tick = client.symbol_info(symbol),client.symbol_info_tick(symbol)
    if info is None or tick is None:
        raise ValueError("Cannot build trade plan without broker specification and quote")
    return {"point":float(info.point),"tick_size":float(info.trade_tick_size),
            "max_spread_points":float(trading_cfg.get("max_spread_points",40)),
            "spread_price":float(tick.ask-tick.bid),
            "commission_per_lot":commission_per_lot(client,symbol,trading_cfg,max(float(tick.ask),float(tick.bid))),
            "commission_at_entry":lambda entry:commission_per_lot(client,symbol,trading_cfg,entry),
            "exit_slippage":float(trading_cfg.get("slippage_points",0))*float(info.point),
            "profit":lambda side,entry,exit:client.order_calc_profit(0 if side=="BUY" else 1,symbol,1.0,entry,exit),
            "cost_basis":"broker_account_currency_estimate"}


def build_trade_plan(frames, direction, regime, strategy, cfg, context=None):
    side = getattr(direction,"value",direction)
    minimum = float(cfg.get("strategy.reward_risk.minimum_net_rr",1.5))
    plan = TradePlan(False,side,minimum_net_rr=minimum)
    if side not in {"BUY","SELL"}:
        plan.reason = "No technical entry; no executable trade plan"
        return plan
    context = context or {}
    x = frames["entry"].iloc[-1]
    atr,close = float(x.atr),float(x.close)
    if not math.isfinite(atr) or atr<=0 or not math.isfinite(close) or close<=0:
        plan.reason = "Invalid ATR/price for trade plan"
        return plan
    sign = 1 if side=="BUY" else -1
    spread = float(context.get("spread_price",0))
    slip = float(context.get("exit_slippage",0))
    commission = float(context.get("commission_per_lot",0))
    tick = float(context.get("tick_size",0))
    if not all(math.isfinite(v) and v>=0 for v in (spread,slip,commission,tick)):
        plan.reason = "Invalid trade-plan costs"
        return plan
    # Input forex bars are Bid. Entry slippage is an explicit conservative estimate.
    entry = close + (spread+slip if side=="BUY" else -slip)
    plan.entry = entry
    if context.get("commission_at_entry"):
        commission = float(context["commission_at_entry"](entry))
        if not math.isfinite(commission) or commission < 0:
            plan.reason = "Invalid entry commission"
            return plan
    buffer = atr*float(cfg.get("strategy.reward_risk.structure_buffer_atr",.1))
    stop_distance = atr*float(cfg.get("strategy.stop_atr_multiple",1.5))
    window = int(cfg.get("strategy.reward_risk.swing_window",3))
    lookback = int(cfg.get("strategy.reward_risk.structure_lookback",100))
    respect_structure = bool(cfg.get("strategy.reward_risk.respect_structure",True))
    entry_highs,entry_lows = _swings(frames["entry"].tail(lookback),window)
    stop_swings = entry_lows if side=="BUY" else entry_highs
    behind = [price for _,price in stop_swings if sign*(entry-price)>0]
    if respect_structure and behind:
        structural = sign*(entry-behind[-1])+buffer
        if structural>stop_distance:
            stop_distance = structural
            plan.stop_source = "Confirmed entry swing plus ATR buffer"
    sl = _snap(entry-sign*stop_distance,tick,side=="SELL")
    distance = sign*(entry-sl)
    plan.sl = sl
    maximum = float(cfg.get("strategy.reward_risk.max_stop_atr",3))
    if sl<=0 or distance<=0 or distance>atr*maximum+1e-9:
        plan.reason = "Structural stop exceeds allowed ATR distance; stop not tightened to force entry"
        return plan
    barriers=[]
    if respect_structure:
        for role in ("entry","setup","trend"):
            highs,lows = _swings(frames[role].tail(lookback),window)
            barriers.extend(price for _,price in (highs if side=="BUY" else lows) if sign*(price-entry)>0)
        if regime==MarketRegime.RANGING and strategy in {"mean_reversion","stochastic_reversal","cci_reversion"}:
            midpoint = float(x.get("bb_mid",float("nan")))
            if math.isfinite(midpoint) and sign*(midpoint-entry)>0:
                barriers.append(midpoint)
    if barriers:
        plan.obstacle = min(barriers,key=lambda v:sign*(v-entry))
    callback = context.get("profit")
    value = float(context.get("contract_value_per_price",1))
    if not math.isfinite(value) or value<=0:
        plan.reason = "Invalid contract conversion"
        return plan
    profit = (lambda start,end:callback(side,start,end)) if callback else (lambda start,end:sign*(end-start)*value)
    if not callback and commission and "contract_value_per_price" not in context:
        plan.reason = "Account-currency conversion required for commission-aware plan"
        return plan
    plan.cost_basis = context.get("cost_basis","historical_constant_contract_value" if "contract_value_per_price" in context else "unverified_price_geometry")
    preferred = float(cfg.get("strategy.reward_risk.range_target",1.5) if regime==MarketRegime.RANGING else
                      cfg.get("strategy.reward_risk.breakout_target",2.5) if regime==MarketRegime.BREAKOUT else
                      cfg.get("strategy.reward_risk.trend_target",2.0))
    requested = cfg.get("strategy.reward_risk.candidates",[1.5,2,2.5,3])
    candidates = sorted(set(float(v) for v in [*requested,preferred,minimum] if minimum<=float(v)<=preferred),reverse=True)
    try:
        probe = profit(entry,entry+sign*atr)
        stop_pnl = profit(entry,sl-sign*slip)
        if probe is None or stop_pnl is None or not math.isfinite(probe) or not math.isfinite(stop_pnl) or probe<=0 or stop_pnl>=0:
            raise ValueError("Account-currency stop/profit estimate unavailable")
        unit_value = probe/atr
        loss = -stop_pnl+commission
        if cfg.get("strategy.experiment","baseline")=="range_midpoint" and regime==MarketRegime.RANGING:
            midpoint = float(x.get("bb_mid",float("nan")))
            target = _snap(midpoint-sign*buffer,tick,side=="SELL")
            if not math.isfinite(target) or sign*(target-entry)<=0:
                raise ValueError("Range midpoint is not a valid target")
            economics = net_reward_risk(side,entry,sl,target,profit,commission,slip)
            if economics["net_rr"]+1e-8<minimum:
                raise ValueError("Range midpoint net reward/risk below unchanged minimum")
            if plan.obstacle is not None and sign*(target-plan.obstacle)>0:
                raise ValueError("Range midpoint target crosses a nearer structure obstacle")
            plan.accepted=True;plan.tp=target;plan.net_rr=economics["net_rr"];plan.gross_rr=economics["gross_rr"]
            plan.target_net_rr=plan.net_rr;plan.break_even_win_rate=economics["break_even_win_rate"]
            plan.loss_per_lot=economics["loss_per_lot"];plan.reward_per_lot=economics["reward_per_lot"]
            plan.reason="Range midpoint target after unchanged cost and risk checks"
            return plan
        for wanted in candidates:
            reward_distance = (wanted*loss+commission)/unit_value+slip
            tp = _snap(entry+sign*reward_distance,tick,side=="BUY")
            row={"target_net_rr":wanted,"tp":tp,"accepted":False}
            if plan.obstacle is not None and sign*(tp-entry)>sign*(plan.obstacle-entry)-buffer+1e-9:
                row["reason"]="Target would cross the nearest confirmed price obstacle"
            else:
                economics = net_reward_risk(side,entry,sl,tp,profit,commission,slip)
                row.update(economics)
                if economics["net_rr"]+1e-8<wanted:
                    row["reason"]="Costs/conversion leave insufficient net reward"
                else:
                    row.update(accepted=True,reason="Net reward target fits before the price obstacle")
                    if not plan.accepted:
                        plan.accepted=True;plan.tp=tp;plan.target_net_rr=wanted
                        plan.gross_rr=economics["gross_rr"];plan.net_rr=economics["net_rr"]
                        plan.break_even_win_rate=economics["break_even_win_rate"]
                        if plan.cost_basis!="unverified_price_geometry":
                            plan.loss_per_lot=economics["loss_per_lot"];plan.reward_per_lot=economics["reward_per_lot"]
            row["cost_basis"] = plan.cost_basis
            if plan.cost_basis=="unverified_price_geometry":
                row.pop("loss_per_lot",None);row.pop("reward_per_lot",None)
            plan.candidates.append(row)
    except ValueError as exc:
        plan.accepted=False;plan.tp=None;plan.net_rr=None;plan.gross_rr=None
        plan.reason=str(exc)
        return plan
    plan.reason = "Selected regime target after costs and structure checks" if plan.accepted else "No target meets minimum net reward/risk before the nearest obstacle"
    return plan
