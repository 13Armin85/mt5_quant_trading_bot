"""Read-only technical snapshot. This module cannot submit orders."""
from datetime import datetime, timezone

from analysis.indicators import add_indicators
from analysis.market_structure import analyze_structure
from analysis.regime_detector import detect_regime
from analysis.volume import analyze_volume
from core.data_feed import DataFeed
from core.symbol_resolver import SymbolResolver
from signals.signal_engine import SignalEngine
from signals.trade_plan import pricing_context
from core.symbol_policy import trading_policy


def scan_symbol(cfg, client, logical):
    symbol = SymbolResolver(client,cfg.symbols).resolve(logical)
    feed = DataFeed(client,int(cfg.get("trading.max_tick_age_seconds",15)),cfg.symbols)
    frames = {}
    for role,timeframe in cfg.get("strategy.timeframes",{}).items():
        frame = feed.bars(symbol,timeframe,int(cfg.get("trading.default_bars",700)),require_fresh=False)
        if frame.empty:
            raise ValueError(f"No {timeframe} history for {symbol}")
        frames[role] = add_indicators(frame,cfg.get("strategy.indicators",{}))
    regime = detect_regime(frames["trend"],cfg.get("strategy.filters",{}))
    structure = analyze_structure(frames["setup"])
    volume = analyze_volume(frames["setup"],cfg.get("strategy.indicators",{}))
    signal = SignalEngine(cfg).generate(symbol,frames,regime,structure,volume,False,
        "Read-only technical snapshot; news not validated at historical decision time",0,feed.spread_points(symbol),pricing_context(client,symbol,trading_policy(symbol,cfg.get("trading",{}),cfg.symbols)))
    return {"symbol":symbol,"logical_symbol":logical,"generated_at":datetime.now(timezone.utc).isoformat(),
            "read_only":True,"orders_enabled":False,"fresh_tick":feed.is_tick_fresh(symbol),
            "regime":regime.value,"candles":{role:str(frame.time.iloc[-1]) for role,frame in frames.items()},
            "final_direction":signal.direction.value,"reasons":signal.reasons,**signal.meta}
