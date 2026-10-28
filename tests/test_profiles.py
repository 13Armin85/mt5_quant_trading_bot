import pytest

from config import load_config
from signals.signal_engine import SignalEngine


def configured():
    cfg = load_config().model_copy(deep=True)
    cfg.settings["profiles"]["enabled"] = True
    return cfg


def test_profile_timeframes_and_signal_revisions_are_independent():
    cfg = configured()
    cfg.validate_runtime()
    scalp, intra = cfg.for_profile("scalping"), cfg.for_profile("intraday")
    assert scalp.get("strategy.timeframes.entry") == "M1"
    assert intra.get("strategy.timeframes.entry") == "M5"
    assert SignalEngine(scalp).revision != SignalEngine(intra).revision
    assert cfg.get("strategy.timeframes.entry") == "M5"


def test_profiles_and_experimental_conflict_cannot_activate_in_live():
    cfg = configured()
    cfg.settings["trading"]["mode"] = "LIVE"
    with pytest.raises(ValueError, match="profiles"):
        cfg.validate_runtime()
    cfg.settings["profiles"]["enabled"] = False
    cfg.settings["strategy"]["selection"]["conflict_policy"] = "quality_margin"
    with pytest.raises(ValueError, match="conflict policy"):
        cfg.validate_runtime()
    cfg.settings["strategy"]["selection"]["conflict_policy"] = "strict"
    cfg.settings["strategy"]["experiment"] = "balanced"
    with pytest.raises(ValueError, match="Balanced"):
        cfg.validate_runtime()


def test_profile_cannot_understate_shared_execution_costs():
    cfg = configured()
    cfg.settings["profiles"]["scalping"]["trading"]["slippage_points"] = 0
    with pytest.raises(ValueError, match="cost assumptions"):
        cfg.validate_runtime()
