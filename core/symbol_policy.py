"""Per-symbol costs; point values always mean broker points."""
import math


ALLOWED_COSTS = {"max_spread_points", "slippage_points", "commission_per_lot", "commission_rate", "max_deviation_points"}


def symbol_metadata(symbol, symbols):
    for logical, meta in symbols.get("symbols", {}).items():
        if symbol in {logical, meta.get("broker_symbol"), *meta.get("aliases", [])}:
            return logical, meta
    return None, None


def trading_policy(symbol, trading, symbols):
    _, meta = symbol_metadata(symbol, symbols)
    return {**trading, **((meta or {}).get("costs") or {})}


def commission_per_lot(client, symbol, policy, entry):
    """Round-trip fixed fee plus a fraction of entry notional for linear CFDs.

    A synthetic CFD loss converts the percentage fee to account currency using
    the broker's contract size and currency conversion, including non-USD accounts.
    This calculation never submits an order.
    """
    fixed = float(policy.get("commission_per_lot", 0))
    rate = float(policy.get("commission_rate", 0))
    if not math.isfinite(fixed) or fixed < 0 or not math.isfinite(rate) or not 0 <= rate < 1:
        raise ValueError("Invalid commission policy")
    if not rate:
        return fixed
    if client is None or not math.isfinite(entry) or entry <= 0:
        raise ValueError("Percentage commission requires broker conversion and a valid entry")
    info = client.symbol_info(symbol)
    if info is None or getattr(info, "trade_calc_mode", None) != 2:
        raise ValueError("Percentage commission requires a linear CFD contract")
    debit = client.order_calc_profit(0, symbol, 1.0, entry, entry * (1 - rate))
    if debit is None or not math.isfinite(debit) or debit >= 0:
        raise ValueError("Account-currency commission conversion failed")
    return fixed - float(debit)
