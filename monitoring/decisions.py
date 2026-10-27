"""Stable decision identities and structured rejection codes."""
import hashlib
import json


def config_revision(cfg):
    return hashlib.sha256(json.dumps({"settings": cfg.settings, "symbols": cfg.symbols,
        "regimes": cfg.strategy}, sort_keys=True, default=str).encode()).hexdigest()[:16]


def evaluation_id(symbol, candle, strategy, revision):
    return hashlib.sha256(f"{symbol}|{candle}|{strategy}|{revision}".encode()).hexdigest()[:32]


def reason_code(reason):
    text = str(reason).lower()
    for code, words in (
        ("KILL_SWITCH", ("kill switch",)),
        ("DUPLICATE", ("duplicate", "existing symbol", "existing paper symbol")),
        ("EXECUTION_UNKNOWN", ("ambiguous", "unresolved", "protection_failure", "unknown")),
        ("DATA_STALE", ("stale", "fresh quote", "unclosed", "clock")),
        ("WARMUP", ("warmup", "warming", "insufficient multi-timeframe")),
        ("DATA_INVALID", ("history", "no data", "missing account", "specification", "no currency", "mapping", "unavailable")),
        ("NEWS", ("news", "economic surprise")),
        ("SPREAD", ("spread",)),
        ("DIRECTION_CONFLICT", ("conflict",)),
        ("REGIME", ("regime", "requires a ranging", "requires a trending")),
        ("SCORE", ("confidence", "selection score", "independent strategy groups")),
        ("STRUCTURE", ("structure", "obstacle", "stop distance", "stops/freeze")),
        ("RR", ("reward/risk", "risk/reward",)),
        ("MARGIN", ("margin",)),
        ("TOTAL_RISK", ("total open risk", "risk budget", "risk exceeds",)),
        ("MIN_LOT", ("minimum", "volume",)),
        ("EXPOSURE", ("concentration", "correlation", "maximum positions")),
        ("DAILY_COUNT", ("trade count",)),
        ("LOSS_PAUSE", ("consecutive losses",)),
        ("LOSS_LIMIT", ("loss limit", "drawdown",)),
        ("COOLDOWN", ("cooldown",)),
        ("NO_SETUP", ("no technical entry", "conditions absent", "no eligible setup",)),
    ):
        if any(word in text for word in words):
            return code
    return "EXECUTION_REJECTED"
