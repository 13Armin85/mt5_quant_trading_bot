from __future__ import annotations

import pandas as pd


class CorrelationGuard:
    def __init__(self, threshold: float = 0.80, max_correlated_positions: int = 1) -> None:
        self.threshold=threshold; self.max_correlated_positions=max_correlated_positions

    def matrix(self, closes: dict[str, pd.Series], lookback: int = 250) -> pd.DataFrame:
        returns={k:v.pct_change().tail(lookback) for k,v in closes.items()}
        return pd.DataFrame(returns).corr()

    def can_open(self, symbol: str, open_symbols: list[str], corr: pd.DataFrame) -> tuple[bool,str]:
        if symbol not in corr.index: return True,"No correlation history"
        correlated=[s for s in open_symbols if s in corr.columns and abs(float(corr.loc[symbol,s]))>=self.threshold]
        if len(correlated)>=self.max_correlated_positions:
            return False,f"Correlated exposure limit: {correlated}"
        return True,"OK"
