"""Causal multi-timeframe signal generation and chronological strategy comparison."""
from copy import deepcopy
import pandas as pd

from analysis.indicators import add_indicators
from analysis.market_structure import analyze_structure
from analysis.regime_detector import detect_regime
from analysis.volume import analyze_volume
from config import AppConfig
from core.data_feed import DataFeed
from signals.signal_engine import SignalEngine
from signals.strategies import FAMILIES
from utils.types import Direction
from analysis.sessions import active_sessions

TF = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


def generate_signals(bars, cfg, contract_value, family, stop_multiple=None, costs=None):
    bars = bars.copy()
    bars["time"] = pd.to_datetime(bars.time, utc=True)
    DataFeed.validate_bars(bars)
    if "symbol" in bars and bars.symbol.nunique()!=1:
        raise ValueError("Generate signals per instrument, then combine symbol-tagged bars/signals for portfolio simulation")
    settings = deepcopy(cfg.settings)
    settings["strategy"]["family"] = family
    if stop_multiple is not None:
        settings["strategy"]["stop_atr_multiple"] = stop_multiple
    local = AppConfig(settings=settings, symbols=cfg.symbols, strategy=cfg.strategy, env=cfg.env)
    costs = costs or {}
    cost_symbol = str(bars.symbol.iloc[0]) if "symbol" in bars else "__single__"
    costs = {**costs, **costs.get("symbol_costs",{}).get(cost_symbol,{})}
    market_context = {"contract_value_per_price":contract_value,"spread_price":float(costs.get("spread",0)),
                      "tick_size":float(costs.get("tick_size") or 0),
                      "exit_slippage":float(costs.get("slippage",0)),"commission_per_lot":float(costs.get("commission_per_lot",0)),
                      "cost_basis":"historical_constant_contract_value"}
    tfs = local.get("strategy.timeframes")
    step = TF[tfs["entry"]]
    diffs = bars.time.diff().dropna().dt.total_seconds()
    if diffs.empty or diffs.min() != step or any(diffs % step):
        raise ValueError("CSV must use configured entry timeframe or a slower strategy entry must be configured")
    all_frames = {}
    indexed = bars.set_index("time")
    indexed.index = indexed.index.tz_convert(cfg.get("trading.mt5_time_zone", "UTC"))
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    agg.update({k: "sum" for k in ("tick_volume", "real_volume") if k in indexed})
    if "spread" in indexed:
        agg["spread"] = "median"
    for role, tf in tfs.items():
        seconds = TF[tf]
        if seconds < step or seconds % step:
            raise ValueError("Research higher timeframes must be multiples of entry timeframe")
        frame = indexed.resample(f"{seconds}s", closed="left", label="right").agg(agg).dropna(subset=["open", "high", "low", "close"])
        counts = indexed.close.resample(f"{seconds}s", closed="left", label="right").count()
        frame = frame.loc[counts.reindex(frame.index).eq(seconds // step)].reset_index()
        frame["time"] = pd.to_datetime(frame.time, utc=True)
        if len(frame) < 220:
            raise ValueError(f"Insufficient {tf} history: need at least 220 complete bars")
        all_frames[role] = add_indicators(frame, local.get("strategy.indicators", {}))
    engine, result = SignalEngine(local), []
    ready = max(df.time.iloc[219] for df in all_frames.values())
    for now in all_frames["entry"].time[all_frames["entry"].time >= ready]:
        ends = {role: df.time.searchsorted(now, side="right") for role, df in all_frames.items()}
        frames = {role: df.iloc[max(0, ends[role]-300):ends[role]] for role, df in all_frames.items()}
        if any(len(df) < 220 for df in frames.values()):
            continue
        regime = detect_regime(frames["trend"], local.get("strategy.filters", {}))
        structure = analyze_structure(frames["setup"])
        volume = analyze_volume(frames["setup"], local.get("strategy.indicators", {}))
        identity_symbol = str(bars.symbol.iloc[0]) if "symbol" in bars else "research"
        signal = engine.generate(identity_symbol, frames, regime, structure, volume, True,
                                 "Historical news not supplied; technical-only research", .5, 0,market_context)
        if signal.direction == Direction.HOLD:
            continue
        result.append({"time": now - pd.Timedelta(seconds=step), "direction": signal.direction.value,
                       "available_at":now,"symbol":"research","strategy":signal.strategy,
                       "confidence":signal.confidence,"regime":regime.value,"net_rr":signal.risk_reward,
                       "session":", ".join(active_sessions(now.to_pydatetime())) or "closed",
                       "entry": signal.entry_price, "sl": signal.stop_loss, "tp": signal.take_profit,
                       "contract_value_per_price": contract_value, "signal_id": signal.signal_id,
                       "minimum_net_rr":signal.meta["trade_plan"]["minimum_net_rr"]})
    # The input single-instrument CSV has no symbol column; keep the simulation key consistent.
    for row in result:
        row["symbol"] = str(bars.symbol.iloc[0]) if "symbol" in bars else "__single__"
    return pd.DataFrame(result, columns=["time", "available_at", "symbol", "strategy", "confidence", "regime", "session", "net_rr", "direction", "entry", "sl", "tp", "contract_value_per_price", "signal_id","minimum_net_rr"])


def compare_strategies(bars, cfg, contract_value, engine_kwargs):
    from backtest.engine import BacktestEngine
    from backtest.walk_forward import build_windows
    bars = bars.copy()
    bars["time"] = pd.to_datetime(bars.time, utc=True)
    first, second = bars.time.iloc[int(len(bars) * .6)], bars.time.iloc[int(len(bars) * .8)]
    report = {"news_validated": False, "selection": "All families reported; no automatic live promotion",
              "split": {"train_end": str(first), "validation_end": str(second)}, "families": {}}
    from backtest.provenance import manifest
    report["manifest"] = manifest(bars,cfg,engine_kwargs)
    windows = build_windows(pd.DatetimeIndex(bars.time))
    for family in (*FAMILIES,"ensemble"):
        signals = generate_signals(bars, cfg, contract_value, family,costs=engine_kwargs)
        results = {}
        for name, start, end in (("train", bars.time.iloc[0], first), ("validation", first, second), ("test", second, bars.time.iloc[-1] + pd.Timedelta(seconds=1))):
            segment = bars[(bars.time >= start) & (bars.time < end)]
            ss = signals[(signals.time >= start) & (signals.time < end)]
            if segment.empty:
                continue
            _, results[name] = BacktestEngine(**engine_kwargs).run(segment, ss)
        results["cost_sensitivity"] = {}
        development = bars[bars.time<second]
        validation_bars = bars[(bars.time>=first)&(bars.time<second)]
        validation_signals = signals[(signals.time>=first)&(signals.time<second)]
        for multiplier in (1, 2):
            costs = dict(engine_kwargs)
            for field in ("spread", "slippage", "commission_per_lot"):
                costs[field] = costs.get(field, 0) * multiplier
            _, results["cost_sensitivity"][str(multiplier)] = BacktestEngine(**costs).run(validation_bars, validation_signals)
        results["parameter_sensitivity"] = {}
        baseline = float(cfg.get("strategy.stop_atr_multiple", 1.5))
        for multiple in (baseline * .8, baseline * 1.2):
            changed = generate_signals(development, cfg, contract_value, family, multiple,costs=engine_kwargs) if len(development)>=220 else signals.iloc[:0]
            changed = changed[(changed.time>=first)&(changed.time<second)]
            _, results["parameter_sensitivity"][str(multiple)] = BacktestEngine(**engine_kwargs).run(validation_bars, changed)
        results["reward_risk_sensitivity"] = {}
        for target in cfg.get("strategy.reward_risk.candidates",[1.5,2,2.5,3]):
            varied = cfg.model_copy(deep=True)
            varied.settings.setdefault("strategy",{}).setdefault("reward_risk",{}).update(range_target=target,trend_target=target,breakout_target=target)
            rr_signals = generate_signals(development,varied,contract_value,family,costs=engine_kwargs) if len(development)>=220 else signals.iloc[:0]
            rr_signals = rr_signals[(rr_signals.time>=first)&(rr_signals.time<second)]
            _,results["reward_risk_sensitivity"][str(target)] = BacktestEngine(**engine_kwargs).run(validation_bars,rr_signals)
        results["sensitivity_basis"] = "Validation only; test remains reserved"
        results["walk_forward"] = []
        for window in windows:
            start, end = pd.Timestamp(window.validation_end, tz="UTC"), pd.Timestamp(window.test_end, tz="UTC")
            segment, ss = bars[(bars.time >= start) & (bars.time < end)], signals[(signals.time >= start) & (signals.time < end)]
            _, metrics = BacktestEngine(**engine_kwargs).run(segment, ss)
            results["walk_forward"].append({"start": str(start), "end": str(end), "metrics": metrics})
        if not windows:
            results["walk_forward_status"] = "Insufficient history for 12/3/3 month windows"
        report["families"][family] = results
    # Rank on validation only; the later test period never chooses a strategy.
    threshold = int(cfg.get("validation.minimum_trades",200))
    ranked = []
    for name,results in report["families"].items():
        metrics = results.get("validation",{})
        if metrics.get("total_trades",0)<threshold or metrics.get("net_profit",0)<=0:
            continue
        if metrics.get("profit_factor",0)<float(cfg.get("validation.minimum_profit_factor",1.3)) or metrics.get("max_drawdown",1)>float(cfg.get("validation.maximum_drawdown",.25)):
            continue
        ranked.append({"strategy":name,"validation_expectancy":metrics["expectancy"],"validation_profit_factor":metrics["profit_factor"],"validation_drawdown":metrics["max_drawdown"]})
    ranked.sort(key=lambda x:(-x["validation_expectancy"],x["validation_drawdown"],x["strategy"]))
    report["validation_ranking"] = ranked
    report["validation_choice"] = ranked[0]["strategy"] if ranked else None
    report["chosen_strategy_test"] = report["families"][ranked[0]["strategy"]].get("test") if ranked else None
    report["selection_status"] = "Validation-only ranking; no live promotion" if ranked else "No strategy met the validation sample/performance requirements"
    return report


def compare_experiments(bars,cfg,contract_value,engine_kwargs):
    """A fixed shortlist, each changing one hypothesis relative to the same baseline."""
    plans = {"trend_pullback":("baseline","h1_context"),"mean_reversion":("baseline","range_midpoint"),
             "breakout":("baseline","breakout_retest")}
    from backtest.engine import BacktestEngine
    from backtest.provenance import manifest
    data = bars.copy();data["time"] = pd.to_datetime(data.time,utc=True)
    first, second = data.time.iloc[int(len(data)*.6)],data.time.iloc[int(len(data)*.8)]
    result = {"manifest":manifest(data,cfg,engine_kwargs),"families":{},"active_strategy":"baseline",
        "selection":"Fixed hypothesis list; no automatic configuration or LIVE promotion",
        "split":{"train_end":str(first),"validation_end":str(second)}}
    for family,experiments in plans.items():
        result["families"][family] = {}
        for experiment in experiments:
            local = cfg.model_copy(deep=True)
            local.settings["strategy"]["experiment"] = experiment
            signals = generate_signals(data,local,contract_value,family,costs=engine_kwargs)
            metrics = {}
            for name,start,end in (("train",data.time.iloc[0],first),("validation",first,second),
                    ("test",second,data.time.iloc[-1]+pd.Timedelta(seconds=1))):
                segment = data[(data.time>=start)&(data.time<end)]
                subset = signals[(signals.time>=start)&(signals.time<end)]
                _,metrics[name] = BacktestEngine(**engine_kwargs).run(segment,subset)
            result["families"][family][experiment] = metrics
    return result
