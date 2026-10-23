from __future__ import annotations

from dataclasses import dataclass
import pandas as pd


@dataclass(slots=True)
class WalkForwardWindow:
    train_start: pd.Timestamp; train_end: pd.Timestamp; validation_end: pd.Timestamp; test_end: pd.Timestamp


def build_windows(index: pd.DatetimeIndex, train_months: int = 12, validation_months: int = 3, test_months: int = 3) -> list[WalkForwardWindow]:
    start = pd.Timestamp(index.min()).tz_localize(None) if pd.Timestamp(index.min()).tzinfo else pd.Timestamp(index.min())
    end = pd.Timestamp(index.max()).tz_localize(None) if pd.Timestamp(index.max()).tzinfo else pd.Timestamp(index.max())
    windows = []
    cursor = start
    while True:
        train_end = cursor + pd.DateOffset(months=train_months)
        val_end = train_end + pd.DateOffset(months=validation_months)
        test_end = val_end + pd.DateOffset(months=test_months)
        if test_end > end: break
        windows.append(WalkForwardWindow(cursor, train_end, val_end, test_end)); cursor += pd.DateOffset(months=test_months)
    return windows
