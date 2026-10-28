from types import SimpleNamespace

import pandas as pd

from backtest.research import generate_signals
from config import load_config
from utils.types import Direction, MarketRegime


def test_m1_history_resamples_to_complete_intraday_bars_without_future_data(monkeypatch):
    bars=pd.DataFrame({"time":pd.date_range("2026-01-01",periods=53000,freq="min",tz="UTC"),
        "open":1.1,"high":1.101,"low":1.099,"close":1.1})
    seen=[]
    class NoTradeEngine:
        def __init__(self,cfg):
            pass
        def generate(self,symbol,frames,*args):
            entry_time=frames["entry"].time.iloc[-1]
            assert all(frame.time.iloc[-1]<=entry_time for frame in frames.values())
            seen.append(entry_time)
            return SimpleNamespace(direction=Direction.HOLD)
    monkeypatch.setattr("backtest.research.SignalEngine",NoTradeEngine)
    monkeypatch.setattr("backtest.research.add_indicators",lambda frame,*args:frame)
    monkeypatch.setattr("backtest.research.detect_regime",lambda *args:MarketRegime.UNCERTAIN)
    monkeypatch.setattr("backtest.research.analyze_structure",lambda *args:{})
    monkeypatch.setattr("backtest.research.analyze_volume",lambda *args:{})
    generated=generate_signals(bars,load_config().for_profile("intraday"),100000,"ensemble")
    assert generated.empty and seen
    assert all(time.minute%5==0 for time in seen)
