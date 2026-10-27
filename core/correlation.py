"""Directional portfolio concentration, independent of news currency mappings."""
import math
import pandas as pd
from core.symbol_policy import symbol_metadata

FX = {"EUR", "USD", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"}


class CorrelationGuard:
    def __init__(self, threshold=.80, max_correlated_positions=1, minimum_history=60, symbols=None):
        self.threshold = threshold
        self.max_correlated_positions = max_correlated_positions
        self.minimum_history = minimum_history
        self.symbols = symbols or {"symbols": {}}
        if not math.isfinite(threshold) or not 0<threshold<=1 or max_correlated_positions<1 or minimum_history<2:
            raise ValueError("Invalid portfolio correlation limits")

    def factors(self, symbol):
        logical, meta = symbol_metadata(symbol, self.symbols)
        if meta is None:
            return None
        if meta.get("risk_factors"):
            return meta["risk_factors"]
        if len(logical) == 6 and logical[:3] in FX and logical[3:] in FX:
            return {logical[:3]: 1, logical[3:]: -1}
        return None

    def matrix(self, closes: dict[str, pd.Series], lookback: int = 250) -> pd.DataFrame:
        for values in closes.values():
            if values.index.has_duplicates or not values.index.is_monotonic_increasing:
                raise ValueError("Correlation history must have unique ordered timestamps")
        prices = pd.DataFrame(closes).tail(lookback + 1)
        return prices.pct_change(fill_method=None).corr(min_periods=self.minimum_history)

    def can_open(self, symbol, open_symbols, corr, direction="BUY"):
        candidate = self.factors(symbol)
        if candidate is None:
            return False, "Missing portfolio risk mapping: " + symbol
        sign = 1 if getattr(direction, "value", direction) == "BUY" else -1
        correlated, fallback = [], False
        for position in open_symbols:
            other, side = position if isinstance(position, tuple) else (position, "BUY")
            factors = self.factors(other)
            if factors is None:
                return False, "Missing portfolio risk mapping: " + other
            other_sign = 1 if getattr(side, "value", side) == "BUY" else -1
            concentration = any(sign * weight * other_sign * factors.get(factor, 0) > 0
                                for factor, weight in candidate.items())
            value = corr.loc[symbol, other] if symbol in corr.index and other in corr.columns else float("nan")
            historical = math.isfinite(value) and value * sign * other_sign >= self.threshold
            fallback |= not math.isfinite(value)
            if concentration or historical or other == symbol:
                correlated.append(other)
        if len(correlated) >= self.max_correlated_positions:
            return False, f"Directional concentration/correlation limit: {sorted(correlated)}"
        return True, "Mapped factor fallback; insufficient aligned history" if fallback else "OK"
