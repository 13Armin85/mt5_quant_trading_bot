"""Chronological OHLC simulation; management decisions take effect on later bars."""
import math
import pandas as pd

from core.data_feed import DataFeed
from core.position_manager import PositionManager
from core.risk_manager import RiskManager
from backtest.metrics import calculate_metrics


class BacktestEngine:
    def __init__(self, initial_equity=10000, spread=0., commission_per_lot=0., slippage=0.,
                 risk_fraction=.01, volume_step=.01, volume_min=.01, volume_max=100,
                 tick_size=None, minimum_net_rr=0, max_total_risk=.01, max_positions=3,
                 max_trades_per_day=10, risk_limits=None, position_management=None, symbol_costs=None, symbols=None):
        values = (initial_equity, spread, commission_per_lot, slippage, risk_fraction,
                  max_total_risk, volume_step, volume_min, volume_max, minimum_net_rr)
        if (not all(math.isfinite(v) for v in values) or initial_equity <= 0
                or min(spread, commission_per_lot, slippage, minimum_net_rr) < 0
                or not 0 < risk_fraction <= .01 or not 0 < max_total_risk <= .01
                or min(volume_step, volume_min) <= 0 or volume_max < volume_min):
            raise ValueError("Invalid backtest capital, costs or broker/risk limits")
        if tick_size is not None and (not math.isfinite(tick_size) or tick_size <= 0):
            raise ValueError("Invalid tick_size")
        if max_positions < 1 or max_trades_per_day < 1:
            raise ValueError("Invalid backtest entry limits")
        self.initial_equity = initial_equity
        self.spread, self.commission_per_lot, self.slippage = spread, commission_per_lot, slippage
        self.risk_fraction, self.max_total_risk = risk_fraction, max_total_risk
        self.volume_step, self.volume_min, self.volume_max = volume_step, volume_min, volume_max
        self.tick_size, self.minimum_net_rr = tick_size, minimum_net_rr
        self.max_positions = max_positions
        self.risk_cfg = {"max_daily_loss": .03, "max_weekly_loss": .06, "max_drawdown": .10,
            "max_consecutive_losses": 3, "paper_loss_pause_resume": "next_day", **(risk_limits or {}),
            "risk_per_trade": risk_fraction, "max_total_risk": max_total_risk,
            "max_trades_per_day": max_trades_per_day}
        self.manager = PositionManager(position_management or {})
        self.symbol_costs = symbol_costs or {}
        allowed_costs = {"spread","slippage","commission_per_lot","volume_step","volume_min","volume_max","tick_size"}
        if not isinstance(self.symbol_costs,dict) or any(not isinstance(v,dict) or set(v)-allowed_costs for v in self.symbol_costs.values()):
            raise ValueError("Historical symbol_costs must use supported price/currency/lot units")
        self.portfolio = None
        if symbols is not None:
            from core.correlation import CorrelationGuard
            self.portfolio = CorrelationGuard(float(self.risk_cfg.get("correlation_threshold",.8)),
                int(self.risk_cfg.get("max_correlated_positions",1)),
                int(self.risk_cfg.get("correlation_minimum_history",60)),symbols)
        self.rejections, self.equity_curve = [], pd.DataFrame()

    def costs(self, symbol):
        result = {"spread": self.spread, "slippage": self.slippage,
            "commission_per_lot": self.commission_per_lot, "volume_step": self.volume_step,
            "volume_min": self.volume_min, "volume_max": self.volume_max, "tick_size": self.tick_size}
        result.update(self.symbol_costs.get(symbol, {}))
        for key, value in result.items():
            if key == "tick_size" and value is None:
                continue
            if not isinstance(value, (int,float)) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid per-symbol historical cost/specification: " + key)
        if min(result["volume_step"], result["volume_min"]) <= 0 or result["volume_max"] < result["volume_min"] or result["tick_size"] == 0:
            raise ValueError("Invalid per-symbol historical volume/tick limits")
        return result

    def reject(self, signal, reason, code):
        self.rejections.append({"time": str(signal["time"]), "symbol": signal["symbol"],
            "signal_id": signal.get("signal_id"), "reason": reason, "reason_code": code})

    @staticmethod
    def liquidation(position, price):
        costs = position["costs"]
        return price + (costs["spread"] if position["direction"] == "SELL" else 0)

    @staticmethod
    def pnl(position, price, volume):
        sign = 1 if position["direction"] == "BUY" else -1
        return (sign * (price - position["entry"]) * position["value"] - position["costs"]["commission_per_lot"]) * volume

    def run(self, bars, signals):
        bars, signals = bars.copy(), signals.copy()
        for frame in (bars, signals):
            frame["time"] = pd.to_datetime(frame["time"], utc=True)
            if "symbol" not in frame:
                frame["symbol"] = "__single__"
        if signals.time.isna().any() or not signals.time.is_monotonic_increasing:
            raise ValueError("Backtest times must be ordered")
        groups = {}
        for symbol, frame in bars.groupby("symbol", sort=True):
            DataFeed.validate_bars(frame)
            frame = frame.copy()
            if "atr" not in frame:
                previous = frame.close.shift()
                true_range = pd.concat([frame.high-frame.low, (frame.high-previous).abs(),
                    (frame.low-previous).abs()],axis=1).max(axis=1)
                # Causal ATR proxy used only for optional trailing; reported explicitly.
                frame["atr"] = true_range.rolling(14,min_periods=14).mean()
            groups[symbol] = frame
        source_times = {symbol:set(frame.time) for symbol,frame in groups.items()}
        entries = {}
        self.rejections, self.equity_curve = [], pd.DataFrame()
        seen = set()
        for signal in signals.to_dict("records"):
            if signal["direction"] not in ("BUY", "SELL"):
                continue
            symbol = signal["symbol"]
            key = signal.get("signal_id", f"{symbol}:{signal['time']}:{signal['direction']}")
            if key in seen:
                continue
            seen.add(key)
            frame = groups.get(symbol)
            if frame is None or signal["time"] not in source_times[symbol]:
                self.reject(signal,"Signal source bar missing from history", "DATA_INVALID")
                continue
            later = frame[frame.time > signal["time"]]
            available = signal.get("available_at")
            if available is not None and not pd.isna(available):
                later = later[later.time >= pd.to_datetime(available,utc=True)]
            if later.empty:
                continue
            decision_time = pd.to_datetime(available,utc=True) if available is not None and not pd.isna(available) else signal["time"]
            if "news_available_at" in signal and pd.to_datetime(signal["news_available_at"],utc=True) > decision_time:
                raise ValueError("Future news snapshot in backtest signal")
            entries.setdefault(later.time.iloc[0],[]).append(signal)
        timeline = sorted(set(bars.time))
        if not timeline:
            return pd.DataFrame(), calculate_metrics(pd.DataFrame(), self.initial_equity)
        now = timeline[0].to_pydatetime()
        risk = RiskManager(self.risk_cfg, clock=lambda: now, mode="BACKTEST")
        risk.initialize_state(self.initial_equity)
        cash, opened, trades, marks, curve = self.initial_equity, [], [], {}, []
        max_concurrent, max_risk_fraction, ambiguities = 0, 0., 0
        lookup = {symbol: frame.set_index("time") for symbol, frame in groups.items()}

        def equity():
            return cash + sum(self.pnl(p, self.liquidation(p, marks[p["symbol"]])-p["sign"]*p["costs"]["slippage"], p["volume"]) for p in opened)

        def close(position, price, timestamp, outcome):
            nonlocal cash
            profit = self.pnl(position,price,position["volume"])
            cash += profit
            total = profit + position["partial_profit"]
            risk.register_closed_trade(total)
            trades.append({"signal_time":position["signal_time"], "entry_time":position["entry_time"],
                "exit_time":timestamp, "symbol":position["symbol"], "strategy":position["strategy"],
                "regime":position["regime"], "session":position["session"], "direction":position["direction"],
                "entry":position["entry"], "exit":price, "volume":position["initial_volume"],
                "risk":position["initial_risk_cash"], "profit":total, "r":total/position["initial_risk_cash"],
                "outcome":outcome, "ambiguous":position["ambiguous"],
                "mae_r":position["mae"]/position["initial_distance"], "mfe_r":position["mfe"]/position["initial_distance"]})
            opened.remove(position)

        for timestamp in timeline:
            now = timestamp.to_pydatetime()
            current = {symbol: frame.loc[timestamp] for symbol,frame in lookup.items() if timestamp in frame.index}
            marks.update({symbol:float(row.open) for symbol,row in current.items()})
            # Opening gaps are knowable before new entries; intrabar extremes are not.
            for p in list(opened):
                if p["symbol"] not in current:
                    continue
                mark = self.liquidation(p,marks[p["symbol"]])
                if p["sign"]*(mark-p["sl"]) <= 0:
                    close(p,mark-p["sign"]*p["costs"]["slippage"],timestamp,"GAP_SL")
                elif p["sign"]*(mark-p["tp"]) >= 0:
                    close(p,p["tp"]-p["sign"]*p["costs"]["slippage"],timestamp,"TP")
            if equity()>0:
                risk.roll_state(equity())
            proposals = sorted(entries.get(timestamp,[]),key=lambda s:(-float(s.get("confidence",0)),
                -float(s.get("net_rr",0)),s["symbol"],str(s.get("strategy","baseline"))))
            for s in proposals:
                symbol, buy = s["symbol"], s["direction"] == "BUY"
                costs = self.costs(symbol)
                sign = 1 if buy else -1
                entry = marks[symbol] + (costs["spread"] if buy else 0) + sign*costs["slippage"]
                sl, tp, value = float(s["sl"]), float(s["tp"]), float(s["contract_value_per_price"])
                if not all(math.isfinite(v) and v > 0 for v in (entry,sl,tp,value)) or not (sl < entry < tp if buy else tp < entry < sl):
                    self.reject(s,"Invalid entry/SL/TP/contract value", "STRUCTURE"); continue
                tick = costs["tick_size"]
                if tick and any(abs(v/tick-round(v/tick))>1e-6 for v in (sl,tp)):
                    self.reject(s,"Stop/target off broker tick grid", "STRUCTURE"); continue
                capital = equity()
                if capital<=0:
                    self.reject(s,"Account equity exhausted after losses/gaps","LOSS_LIMIT");continue
                exposure = sum(p["reserved_risk"] for p in opened)
                budget = min(capital*self.risk_fraction, capital*self.max_total_risk-exposure)
                loss_per_lot = (abs(entry-sl)+costs["slippage"])*value+costs["commission_per_lot"]
                reward = (abs(tp-entry)-costs["slippage"])*value-costs["commission_per_lot"]
                minimum = float(s.get("minimum_net_rr",0))
                if not math.isfinite(minimum) or minimum<0 or reward/loss_per_lot+1e-8 < max(minimum,self.minimum_net_rr):
                    self.reject(s,"Net reward/risk below minimum after next-bar fill and costs", "RR"); continue
                raw = min(max(0,budget)/loss_per_lot,costs["volume_max"],float(s.get("volume",costs["volume_max"])))
                volume = RiskManager._floor_step(raw,costs["volume_step"])
                if not math.isfinite(volume) or volume < costs["volume_min"]:
                    self.reject(s,"Insufficient aggregate risk budget/minimum volume", "TOTAL_RISK"); continue
                if any(p["symbol"]==symbol for p in opened):
                    self.reject(s,"Existing symbol exposure", "DUPLICATE"); continue
                if len(opened) >= self.max_positions:
                    self.reject(s,"Maximum positions reached", "EXPOSURE"); continue
                if self.portfolio is not None and symbol != "__single__":
                    histories = {name:frame.loc[frame.index<timestamp,"close"] for name,frame in lookup.items()}
                    matrix = self.portfolio.matrix(histories,int(self.risk_cfg.get("correlation_lookback",250)))
                    allowed, reason = self.portfolio.can_open(symbol,[(p["symbol"],p["direction"]) for p in opened],matrix,s["direction"])
                    if not allowed:
                        self.reject(s,reason,"EXPOSURE");continue
                allowed, reason = risk.can_trade(capital,exposure/capital)
                if not allowed:
                    from monitoring.decisions import reason_code
                    self.reject(s,reason,reason_code(reason)); continue
                reserved = volume*loss_per_lot
                opened.append(dict(symbol=symbol,direction=s["direction"],sign=sign,entry=entry,
                    sl=sl,tp=tp,value=value,volume=volume,initial_volume=volume,costs=costs,
                    initial_distance=abs(entry-sl),initial_risk_cash=reserved,reserved_risk=reserved,
                    entry_time=timestamp,signal_time=s["time"],strategy=s.get("strategy","baseline"),
                    regime=s.get("regime","UNCERTAIN"),session=s.get("session","unknown"),
                    partial_done=False,partial_profit=0.,mae=0.,mfe=0.,ambiguous=False))
                risk.state.trades_today += 1
                max_concurrent = max(max_concurrent,len(opened))
                max_risk_fraction = max(max_risk_fraction,sum(p["reserved_risk"] for p in opened)/capital)
            for p in list(opened):
                row = current.get(p["symbol"])
                if row is None:
                    continue
                low, high = self.liquidation(p,float(row.low)), self.liquidation(p,float(row.high))
                hit_sl = low <= p["sl"] if p["sign"]==1 else high >= p["sl"]
                hit_tp = high >= p["tp"] if p["sign"]==1 else low <= p["tp"]
                if hit_sl or hit_tp:
                    if hit_sl and hit_tp:
                        ambiguities += 1; p["ambiguous"] = True
                    price = (p["sl"] if hit_sl else p["tp"])-p["sign"]*p["costs"]["slippage"]
                    move = p["sign"]*(price-p["entry"])
                    p["mae"], p["mfe"] = max(p["mae"],-move),max(p["mfe"],move)
                    close(p,price,timestamp,"SL" if hit_sl else "TP")
                    continue
                adverse = p["entry"]-low if p["sign"]==1 else high-p["entry"]
                favorable = high-p["entry"] if p["sign"]==1 else p["entry"]-low
                p["mae"], p["mfe"] = max(p["mae"],adverse),max(p["mfe"],favorable)
                mark = self.liquidation(p,float(row.close))
                atr = float(row.atr) if pd.notna(row.atr) else None
                decision = self.manager.decide(p["direction"],p["entry"],mark,p["sl"],p["initial_distance"],atr,p["partial_done"])
                if decision.close:
                    close(p,mark-p["sign"]*p["costs"]["slippage"],timestamp,decision.reason)
                elif decision.partial_close_fraction:
                    volume = RiskManager._floor_step(p["volume"]*decision.partial_close_fraction,p["costs"]["volume_step"])
                    if min(volume,p["volume"]-volume)+1e-9 >= p["costs"]["volume_min"]:
                        profit = self.pnl(p,mark-p["sign"]*p["costs"]["slippage"],volume)
                        cash += profit; p["partial_profit"] += profit
                        p["reserved_risk"] *= (p["volume"]-volume)/p["volume"]
                        p["volume"] -= volume; p["partial_done"] = True
                elif decision.new_sl is not None:
                    tick = p["costs"]["tick_size"]
                    candidate = round(decision.new_sl/tick)*tick if tick else decision.new_sl
                    if p["sign"]*(candidate-p["sl"])>0 and p["sign"]*(mark-candidate)>0:
                        p["sl"] = candidate # Applied after this bar's SL/TP test, never retroactively.
                if p in opened and timestamp == groups[p["symbol"]].time.iloc[-1]:
                    close(p,mark-p["sign"]*p["costs"]["slippage"],timestamp,"END_OF_DATA")
            marks.update({symbol:float(row.close) for symbol,row in current.items()})
            curve.append({"time":timestamp,"equity":equity(),"realized_equity":cash,
                "open_risk":sum(p["reserved_risk"] for p in opened),"open_positions":len(opened),
                "risk_status":risk.state.status})
        result = pd.DataFrame(trades)
        self.equity_curve = pd.DataFrame(curve)
        if not result.empty:
            result = result.sort_values(["exit_time","symbol"],kind="stable").reset_index(drop=True)
        metrics = calculate_metrics(result,self.initial_equity,self.equity_curve)
        metrics.update(max_concurrent_positions=max_concurrent,maximum_planned_risk_fraction=max_risk_fraction,
            ambiguous_bars=ambiguities,news_validated=False,cost_status="exploratory_unverified",
            live_ready=False,mae_mfe_basis="Completed bars before exit plus exit fill; intrabar sequence unknown",
            exit_time_basis="Opening timestamp of the bar containing the exit; intrabar exit time unknown",
            portfolio_model="Directional factors and past aligned returns" if self.portfolio else "Not supplied; gross risk/position caps only",
            management_basis="Close snapshots; stop changes effective from next bar; causal 14-bar ATR proxy",
            limitations=["No historical margin/conversion/swap verification", "No historical news snapshots",
                "No higher-timeframe thesis invalidation replay", "OHLC close snapshots omit intrabar management opportunities",
                "Bar-close mark-to-market omits intrabar equity extremes"])
        return result, metrics
