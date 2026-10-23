from __future__ import annotations

from datetime import datetime, timezone

from analysis.momentum import score_momentum
from analysis.price_action import analyze_price_action
from analysis.trend import score_trend
from analysis.volatility import score_volatility
from signals.confidence_engine import ConfidenceEngine
from signals.filters import signal_id
from signals.selector import select_strategy, indicator_snapshot
from utils.types import Direction, MarketRegime, Signal


class SignalEngine:
    def __init__(self, app_cfg) -> None:
        self.cfg = app_cfg
        self.confidence = ConfidenceEngine(app_cfg.get("strategy.weights", {}))

    def generate(self, symbol, frames, regime, structure, volume, news_ok, news_reason, news_score, spread_points):
        macro_score, macro_reasons = score_trend(frames["macro"])
        trend_score, trend_reasons = score_trend(frames["trend"])
        momentum_score, momentum_reasons = score_momentum(frames["setup"])
        vol_score, vol_reasons = score_volatility(frames["setup"])
        pa = analyze_price_action(frames["entry"])
        structure_score = float(structure["score"])
        volume_score = float(volume["score"])
        combined_trend = (macro_score + trend_score) / 2
        selection = select_strategy(frames, regime, self.cfg, structure)
        direction, family, rule_reason = selection.direction, selection.strategy, selection.reason
        components = {
            "trend": combined_trend, "structure": structure_score, "momentum": momentum_score,
            "volume": volume_score, "price_action": pa["score"], "volatility": vol_score,
            "news": news_score if news_ok else 0.0,
        }
        technical_score = self.confidence.score(components, direction.value)
        selection_weight = float(self.cfg.get("strategy.selection.rule_weight", .70))
        confidence = selection_weight * selection.score + (1-selection_weight) * technical_score
        x = frames["entry"].iloc[-1]
        entry = float(x.close); atr = float(x.atr)
        stop_mult = float(self.cfg.get("strategy.stop_atr_multiple", 1.5))
        if direction == Direction.BUY:
            sl = entry - atr * stop_mult; tp = entry + (entry - sl) * float(self.cfg.get("trading.preferred_rr", 2.0))
        elif direction == Direction.SELL:
            sl = entry + atr * stop_mult; tp = entry - (sl - entry) * float(self.cfg.get("trading.preferred_rr", 2.0))
        else:
            sl = tp = None
        min_conf = float(self.cfg.get("trading.min_confidence", 0.70))
        reasons = [rule_reason, f"Selected strategy={family}; independent groups={selection.groups}; suitability={selection.score:.3f}"] + macro_reasons + trend_reasons + structure["reasons"] + momentum_reasons + volume["reasons"] + pa["reasons"] + vol_reasons + [news_reason]
        if not news_ok:
            reasons.append("High-impact/news-data safety block"); direction = Direction.HOLD
        if spread_points is None or spread_points > float(self.cfg.get("trading.max_spread_points", 40)):
            reasons.append("Spread safety block"); direction = Direction.HOLD
        if confidence < min_conf: reasons.append(f"Confidence {confidence:.2f} < {min_conf:.2f}"); direction = Direction.HOLD
        rr = abs((tp-entry)/(entry-sl)) if direction != Direction.HOLD and sl != entry else None
        if rr is not None and rr < float(self.cfg.get("trading.min_rr", 1.5)):
            reasons.append("Risk/reward below minimum"); direction = Direction.HOLD
        candle_time = frames["entry"].time.iloc[-1].to_pydatetime() if hasattr(frames["entry"].time.iloc[-1], "to_pydatetime") else datetime.now(timezone.utc)
        identity = "ensemble-v1" if self.cfg.get("strategy.family", "ensemble") in ("auto", "ensemble") else family
        sid = signal_id(symbol, self.cfg.get("strategy.timeframes.entry", "M5"), direction.value, candle_time, identity)
        if direction == Direction.HOLD:
            sl = tp = rr = None
        return Signal(symbol=symbol, direction=direction, confidence=confidence, entry_price=entry, stop_loss=sl, take_profit=tp,
                      risk_reward=rr, market_regime=regime, trend_score=combined_trend, momentum_score=momentum_score,
                      volume_score=volume_score, structure_score=structure_score, price_action_score=pa["score"],
                      news_score=news_score, volatility_score=vol_score, spread_score=0.5 if spread_points is None else max(0.0, 1.0-min(spread_points/100,1)),
                      reasons=reasons, timeframe=self.cfg.get("strategy.timeframes.entry", "M5"), candle_time=candle_time, strategy=family, signal_id=sid,
                      meta={"selection":selection.to_dict(),"indicators":indicator_snapshot(frames),"technical_score":technical_score})
