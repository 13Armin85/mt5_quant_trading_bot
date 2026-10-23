from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database.models import Base, SignalRecord, Trade, ExecutionAttempt, RuntimeState


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
            s.add(SignalRecord(signal_id=signal.signal_id, symbol=signal.symbol, direction=signal.direction.value, confidence=signal.confidence, payload=payload))
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                return False
        return True

    def save_execution(self, signal, lot: float, result) -> None:
        with Session(self.engine) as s:
            s.add(Trade(signal_id=signal.signal_id or "", symbol=signal.symbol, direction=signal.direction.value,
                        entry=result.filled_price or signal.entry_price, sl=signal.stop_loss, tp=signal.take_profit, lot=result.filled_volume or lot,
                        risk=result.planned_risk, confidence=signal.confidence, market_regime=signal.market_regime.value,
                        spread=None, volume_score=signal.volume_score, trend_score=signal.trend_score, news_score=signal.news_score,
                        reason="; ".join(signal.reasons + [result.message]), ticket=result.ticket, status=result.status, success=result.success)); s.commit()

    def open_risk_fraction(self, equity: float) -> float:
        if equity <= 0: return 1.0
        with Session(self.engine) as s:
            rows = s.scalars(select(Trade).where(Trade.status == "OPEN")).all()
        risk = 0.0
        for t in rows:
            if t.risk is None:
                return 1.0
            risk += t.risk
        return min(1.0, risk / equity)

    def recent_trades(self, limit: int = 50):
        with Session(self.engine) as s:
            return list(s.scalars(select(Trade).order_by(Trade.id.desc()).limit(limit)).all())

    def claim_execution(self, key, payload):
        with Session(self.engine) as s:
            s.add(ExecutionAttempt(key=key, status="SUBMITTING", payload=json.dumps(payload)))
            try:
                s.commit()
                return True
            except IntegrityError:
                s.rollback()
                return False

    def update_attempt(self, key, status, payload=None):
        with Session(self.engine) as s:
            row = s.get(ExecutionAttempt, key)
            row.status = status
            row.updated = datetime.now(timezone.utc)
            if payload is not None:
                row.payload = json.dumps(payload)
            s.commit()

    def attempts(self, prefix):
        with Session(self.engine) as s:
            rows = s.scalars(select(ExecutionAttempt)).all()
            return [r for r in rows if r.key.startswith(prefix + ":")]

    def get_state(self, key, default=None):
        with Session(self.engine) as s:
            row = s.get(RuntimeState, key)
            return json.loads(row.payload) if row else default

    def set_state(self, key, value):
        with Session(self.engine) as s:
            s.merge(RuntimeState(key=key, payload=json.dumps(value)))
            s.commit()

    def close_trade(self, ticket, price, profit, status="CLOSED"):
        with Session(self.engine) as s:
            rows = s.scalars(select(Trade).where(Trade.ticket == ticket, Trade.status.in_(["OPEN", "PAPER_OPEN", "PARTIAL"]))).all()
            for row in rows:
                row.exit_price, row.profit, row.status = price, profit, status
                row.profit_r = profit / row.risk if row.risk else None
            s.commit()

    def settle_paper(self, key, scope, ticket, price, profit):
        # One transaction prevents double-booking realized P/L after a restart.
        with Session(self.engine) as s:
            attempt = s.get(ExecutionAttempt, key)
            if attempt.status != "PAPER_OPEN":
                return
            attempt.status = "PAPER_CLOSED"
            state_key = scope + ":paper_pnl"
            state = s.get(RuntimeState, state_key)
            if state is None:
                s.add(RuntimeState(key=state_key, payload=json.dumps(profit)))
            else:
                state.payload = json.dumps(json.loads(state.payload) + profit)
            for trade in s.scalars(select(Trade).where(Trade.ticket == ticket, Trade.status == "PAPER_OPEN")):
                total = profit + json.loads(attempt.payload).get("partial_realized", 0)
                trade.exit_price, trade.profit, trade.status = price, total, "PAPER_CLOSED"
                trade.profit_r = total / trade.risk if trade.risk else None
            s.commit()

    def partial_paper(self, key, scope, payload, profit):
        with Session(self.engine) as s:
            attempt = s.get(ExecutionAttempt, key)
            original = json.loads(attempt.payload)
            if original.get("partial_done"):
                return
            payload["partial_realized"] = profit
            attempt.payload = json.dumps(payload)
            state_key = scope + ":paper_pnl"
            state = s.get(RuntimeState, state_key)
            if state is None:
                s.add(RuntimeState(key=state_key, payload=json.dumps(profit)))
            else:
                state.payload = json.dumps(json.loads(state.payload) + profit)
            s.commit()
