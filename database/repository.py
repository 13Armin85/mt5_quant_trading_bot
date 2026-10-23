from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from database.models import Base, SignalRecord, Trade


class Repository:
    def __init__(self, url: str) -> None:
        self.engine = create_engine(url)
        Base.metadata.create_all(self.engine)

    def signal_exists(self, signal_id: str) -> bool:
        with Session(self.engine) as s:
            return s.scalar(select(SignalRecord.id).where(SignalRecord.signal_id == signal_id)) is not None

    def save_signal(self, signal) -> bool:
        if not signal.signal_id or self.signal_exists(signal.signal_id):
            return False
        payload = json.dumps({
            "reasons": signal.reasons, "regime": signal.market_regime.value,
            "scores": {"trend": signal.trend_score, "momentum": signal.momentum_score, "volume": signal.volume_score,
                       "structure": signal.structure_score, "price_action": signal.price_action_score, "news": signal.news_score,
                       "volatility": signal.volatility_score, "spread": signal.spread_score}}, ensure_ascii=False)
        with Session(self.engine) as s:
            s.add(SignalRecord(signal_id=signal.signal_id, symbol=signal.symbol, direction=signal.direction.value, confidence=signal.confidence, payload=payload)); s.commit()
        return True

    def save_execution(self, signal, lot: float, result) -> None:
        with Session(self.engine) as s:
            s.add(Trade(signal_id=signal.signal_id or "", symbol=signal.symbol, direction=signal.direction.value,
                        entry=signal.entry_price, sl=signal.stop_loss, tp=signal.take_profit, lot=lot,
                        risk=None, confidence=signal.confidence, market_regime=signal.market_regime.value,
                        spread=None, volume_score=signal.volume_score, trend_score=signal.trend_score, news_score=signal.news_score,
                        reason="; ".join(signal.reasons), ticket=result.ticket, status="OPEN" if result.success else "REJECTED", success=result.success)); s.commit()

    def open_risk_fraction(self, equity: float) -> float:
        if equity <= 0: return 1.0
        with Session(self.engine) as s:
            rows = s.scalars(select(Trade).where(Trade.status == "OPEN")).all()
        risk = 0.0
        for t in rows:
            if t.entry is not None and t.sl is not None and t.lot:
                risk += abs(t.entry - t.sl) * t.lot
        return min(1.0, risk / equity)

    def recent_trades(self, limit: int = 50):
        with Session(self.engine) as s:
            return list(s.scalars(select(Trade).order_by(Trade.id.desc()).limit(limit)).all())
