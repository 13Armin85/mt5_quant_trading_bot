from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TradingMode(str, Enum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class MarketRegime(str, Enum):
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT = "BREAKOUT"
    NEWS_EVENT = "NEWS_EVENT"
    UNCERTAIN = "UNCERTAIN"


@dataclass(slots=True)
class Signal:
    symbol: str
    direction: Direction
    confidence: float
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward: float | None = None
    market_regime: MarketRegime = MarketRegime.UNCERTAIN
    trend_score: float = 0.0
    momentum_score: float = 0.0
    volume_score: float = 0.0
    structure_score: float = 0.0
    price_action_score: float = 0.0
    news_score: float = 0.0
    volatility_score: float = 0.0
    spread_score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    timeframe: str = "M5"
    candle_time: datetime | None = None
    strategy: str = "baseline"
    signal_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OrderRequest:
    symbol: str
    direction: Direction
    volume: float
    price: float
    sl: float
    tp: float
    deviation: int
    magic: int = 260912
    comment: str = "quant-bot"
    signal_id: str | None = None
    minimum_net_rr: float | None = None


@dataclass(slots=True)
class ExecutionResult:
    success: bool
    ticket: int | None
    retcode: int | None
    message: str
    filled_price: float | None = None
    raw: Any = None
    status: str = "REJECTED"
    filled_volume: float = 0.0
    planned_risk: float | None = None
