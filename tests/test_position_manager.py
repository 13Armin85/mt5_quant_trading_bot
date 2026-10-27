from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from config import load_config
from core.position_manager import PositionManager


def reversal_frames(direction="BUY"):
    buy = direction == "BUY"
    times = pd.date_range(datetime.now(timezone.utc) - timedelta(minutes=20), periods=3, freq="5min")
    entry = pd.DataFrame({"time": times, "close": [99 if buy else 101] * 3,
                          "ema_20": [100] * 3, "rsi": [40 if buy else 60] * 3,
                          "macd_hist": [-1 if buy else 1] * 3, "atr": [1] * 3})
    trend = pd.DataFrame([{"ema_20": 98 if buy else 102, "ema_50": 100, "ema_200": 102 if buy else 98}])
    return {"entry": entry, "trend": trend, "setup": pd.DataFrame([{"close": 100}])}


@pytest.mark.parametrize("direction,current", [("BUY", 99.25), ("SELL", 100.75)])
def test_loss_limit_exits_before_profit_management(direction, current):
    manager = PositionManager({"loss_cut_enabled": True, "max_adverse_r": .75, "break_even_enabled": True})
    decision = manager.decide(direction, 100, current, 99 if direction == "BUY" else 101, 1)
    assert decision.close and decision.new_sl is None and "adverse" in decision.reason


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_confirmed_reversal_closes_even_profitable_position(direction):
    manager = PositionManager({"invalidation_exit_enabled": True})
    frames = reversal_frames(direction)
    reason = manager.invalidation_reason(direction, frames, frames["entry"].time.iloc[0].isoformat())
    assert reason and "2 closed bars" in reason
    current = 101 if direction == "BUY" else 99
    assert manager.decide(direction, 100, current, 98 if direction == "BUY" else 102, 2,
                          invalidation_reason=reason).close


@pytest.mark.parametrize("case", ["one_bar", "before_entry", "missing", "nan", "neutral_context", "disabled"])
def test_incomplete_reversal_evidence_does_not_exit(case):
    manager = PositionManager({"invalidation_exit_enabled": case != "disabled"})
    frames = reversal_frames()
    opened = frames["entry"].time.iloc[0].isoformat()
    if case == "one_bar":
        frames["entry"].loc[1, "rsi"] = 50
    elif case == "before_entry":
        opened = frames["entry"].time.iloc[-1].isoformat()
    elif case == "missing":
        frames["entry"] = frames["entry"].drop(columns="macd_hist")
    elif case == "nan":
        frames["entry"].loc[2, "rsi"] = float("nan")
    elif case == "neutral_context":
        frames["trend"].loc[0, "ema_20"] = 103
    assert manager.invalidation_reason("BUY", frames, opened) is None


def test_opposing_structure_can_confirm_reversal_without_opposing_trend(monkeypatch):
    frames = reversal_frames()
    frames["trend"].loc[0, "ema_20"] = 103
    frames["setup"] = pd.DataFrame([{"high": 101, "low": 99, "close": 100}])
    monkeypatch.setattr("core.position_manager.analyze_structure", lambda *_: {"direction": "BEARISH"})
    manager = PositionManager({"invalidation_exit_enabled": True})
    assert "structure" in manager.invalidation_reason("BUY", frames, frames["entry"].time.iloc[0])


@pytest.mark.parametrize("key,value", [("max_adverse_r", 0), ("max_adverse_r", 1.1),
    ("max_adverse_r", float("nan")), ("invalidation_confirmation_bars", 1),
    ("invalidation_confirmation_bars", 2.5), ("defensive_exit_cooldown_seconds", -1)])
def test_invalid_protection_settings_are_rejected(key, value):
    cfg = load_config().model_copy(deep=True)
    cfg.settings["position_management"][key] = value
    with pytest.raises(ValueError, match="position_management"):
        cfg.validate_runtime()
