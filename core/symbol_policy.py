"""Per-symbol costs; point values always mean broker points."""
ALLOWED_COSTS = {"max_spread_points", "slippage_points", "commission_per_lot", "max_deviation_points"}


def symbol_metadata(symbol, symbols):
    for logical, meta in symbols.get("symbols", {}).items():
        if symbol in {logical, meta.get("broker_symbol"), *meta.get("aliases", [])}:
            return logical, meta
    return None, None


def trading_policy(symbol, trading, symbols):
    _, meta = symbol_metadata(symbol, symbols)
    return {**trading, **((meta or {}).get("costs") or {})}
