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

TF = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


def generate_signals(bars, cfg, contract_value, family, stop_multiple=None):
    bars = bars.copy()
    bars["time"] = pd.to_datetime(bars.time, utc=True)
    DataFeed.validate_bars(bars)
    settings = deepcopy(cfg.settings)
    settings["strategy"]["family"] = family
    if stop_multiple is not None:
        settings["strategy"]["stop_atr_multiple"] = stop_multiple
    local = AppConfig(settings=settings, symbols=cfg.symbols, strategy=cfg.strategy, env=cfg.env)
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
        frame = frame[counts == seconds // step].reset_index()
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
        signal = engine.generate("research", frames, regime, structure, volume, True,
                                 "Historical news not supplied; technical-only research", .5, 0)
        if signal.direction == Direction.HOLD:
            continue
        result.append({"time": now - pd.Timedelta(seconds=step), "direction": signal.direction.value,
                       "entry": signal.entry_price, "sl": signal.stop_loss, "tp": signal.take_profit,
                       "contract_value_per_price": contract_value, "signal_id": signal.signal_id})
    return pd.DataFrame(result, columns=["time", "direction", "entry", "sl", "tp", "contract_value_per_price", "signal_id"])


def compare_strategies(bars, cfg, contract_value, engine_kwargs):
    from backtest.engine import BacktestEngine
    from backtest.walk_forward import build_windows
    bars = bars.copy()
    bars["time"] = pd.to_datetime(bars.time, utc=True)
    first, second = bars.time.iloc[int(len(bars) * .6)], bars.time.iloc[int(len(bars) * .8)]
    report = {"news_validated": False, "selection": "All families reported; no automatic live promotion",
              "split": {"train_end": str(first), "validation_end": str(second)}, "families": {}}
    windows = build_windows(pd.DatetimeIndex(bars.time))
    for family in (*FAMILIES,"ensemble"):
        signals = generate_signals(bars, cfg, contract_value, family)
        results = {}
        for name, start, end in (("train", bars.time.iloc[0], first), ("validation", first, second), ("test", second, bars.time.iloc[-1] + pd.Timedelta(seconds=1))):
            segment = bars[(bars.time >= start) & (bars.time < end)]
            ss = signals[(signals.time >= start) & (signals.time < end)]
            if segment.empty:
                continue
            _, results[name] = BacktestEngine(**engine_kwargs).run(segment, ss)
        results["cost_sensitivity"] = {}
        for multiplier in (1, 2):
            costs = dict(engine_kwargs)
            for field in ("spread", "slippage", "commission_per_lot"):
                costs[field] = costs.get(field, 0) * multiplier
            _, results["cost_sensitivity"][str(multiplier)] = BacktestEngine(**costs).run(bars, signals)
        results["parameter_sensitivity"] = {}
        baseline = float(cfg.get("strategy.stop_atr_multiple", 1.5))
        for multiple in (baseline * .8, baseline * 1.2):
            changed = generate_signals(bars, cfg, contract_value, family, multiple)
            _, results["parameter_sensitivity"][str(multiple)] = BacktestEngine(**engine_kwargs).run(bars, changed)
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
