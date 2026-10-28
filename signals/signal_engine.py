from __future__ import annotations

from datetime import datetime, timezone

from analysis.momentum import score_momentum
from analysis.price_action import analyze_price_action
from analysis.trend import score_trend
from analysis.volatility import score_volatility
from signals.confidence_engine import ConfidenceEngine
from signals.filters import signal_id
from signals.selector import select_strategy, select_assessments, indicator_snapshot
from signals.trade_plan import build_trade_plan
from monitoring.decisions import config_revision, reason_code
from utils.types import Direction, MarketRegime, Signal


class SignalEngine:
    def __init__(self, app_cfg) -> None:
        self.cfg = app_cfg
        self.confidence = ConfidenceEngine(app_cfg.get("strategy.weights", {}))
        self.revision = config_revision(app_cfg)

    def generate(self, symbol, frames, regime, structure, volume, news_ok, news_reason, news_score, spread_points, market_context=None):
        macro_score, macro_reasons = score_trend(frames["macro"])
        trend_score, trend_reasons = score_trend(frames["trend"])
        momentum_score, momentum_reasons = score_momentum(frames["setup"])
        vol_score, vol_reasons = score_volatility(frames["setup"])
        pa = analyze_price_action(frames["entry"])
        structure_score = float(structure["score"])
        volume_score = float(volume["score"])
        combined_trend = (macro_score + trend_score) / 2
        components = {
            "trend": combined_trend, "structure": structure_score, "momentum": momentum_score,
            "volume": volume_score, "price_action": pa["score"], "volatility": vol_score,
            "news": news_score if news_ok else 0.0,
        }
        preliminary = select_strategy(frames, regime, self.cfg, structure)
        selection_weight = float(self.cfg.get("strategy.selection.rule_weight", .70))
        x = frames["entry"].iloc[-1]
        min_conf = float(self.cfg.get("trading.min_confidence", 0.70))
        minimum_score = float(self.cfg.get("strategy.selection.minimum_score", .65))
        candidate_plans, executable, plans = [], [], {}
        for assessment in sorted(preliminary.assessments, key=lambda a:(-a.score,a.strategy)):
            if not assessment.eligible or assessment.direction == Direction.HOLD or assessment.score < minimum_score:
                continue
            if self.cfg.get("strategy.family", "ensemble") not in ("auto", "ensemble", assessment.strategy):
                continue
            candidate_plan = build_trade_plan(frames, assessment.direction, regime,
                assessment.strategy, self.cfg, market_context)
            candidate_technical = self.confidence.score(components, assessment.direction.value)
            candidate_confidence = selection_weight * assessment.score + (1-selection_weight) * candidate_technical
            accepted = candidate_plan.accepted
            candidate_plans.append({"strategy": assessment.strategy, "group": assessment.group,
                "direction": assessment.direction.value, "suitability": assessment.score,
                "confidence": candidate_confidence, "accepted": accepted,
                "confidence_threshold": min_conf,
                "reason": candidate_plan.reason if not accepted else "Executable trade plan",
                "trade_plan": candidate_plan.to_dict()})
            if accepted:
                executable.append(assessment)
                plans[assessment.strategy] = candidate_plan
        strict_conflict = (self.cfg.get("strategy.selection.conflict_policy", "strict") == "strict"
            and "Conflicting BUY/SELL" in preliminary.reason)
        if strict_conflict:
            selection = preliminary
        else:
            family_setting = self.cfg.get("strategy.family", "ensemble")
            selection = select_assessments(executable, minimum_score,
                int(self.cfg.get("strategy.selection.minimum_groups", 1)) if family_setting in ("auto", "ensemble") else 1,
                self.cfg.get("strategy.selection.conflict_policy", "strict"),
                float(self.cfg.get("strategy.selection.conflict_quality_margin", .20)))
            selection.assessments = preliminary.assessments
            if not executable and candidate_plans:
                selection.reason = "No executable trade plan"
        direction, family, rule_reason = selection.direction, selection.strategy, selection.reason
        technical_score = self.confidence.score(components, direction.value)
        confidence = selection_weight * selection.score + (1-selection_weight) * technical_score
        plan = plans.get(family) or build_trade_plan(frames,direction,regime,family,self.cfg,market_context)
        entry = plan.entry if plan.entry is not None else float(x.close)
        sl,tp = plan.sl,plan.tp
        reasons = [rule_reason, f"Selected strategy={family}; independent groups={selection.groups}; suitability={selection.score:.3f}",plan.reason] + macro_reasons + trend_reasons + structure["reasons"] + momentum_reasons + volume["reasons"] + pa["reasons"] + vol_reasons + [news_reason]
        codes = []
        if selection.direction == Direction.HOLD:
            if "Conflicting" in rule_reason:
                codes.append("DIRECTION_CONFLICT")
            elif candidate_plans and not any(p["accepted"] for p in candidate_plans):
                codes.append(reason_code(candidate_plans[0]["reason"]))
            else:
                codes.append("SCORE" if any(a.eligible for a in selection.assessments) else "NO_SETUP")
        if not plan.accepted:
            codes.append(reason_code(plan.reason))
            direction = Direction.HOLD
        if not news_ok:
            codes.insert(0,"NEWS")
            reasons.append("High-impact/news-data safety block"); direction = Direction.HOLD
        if spread_points is None or spread_points > float((market_context or {}).get("max_spread_points", self.cfg.get("trading.max_spread_points", 40))):
            codes.insert(0,"SPREAD")
            reasons.append("Spread safety block"); direction = Direction.HOLD
        if confidence < min_conf:
            codes.append("SCORE")
            reasons.append(f"Confidence {confidence:.2f} < {min_conf:.2f}"); direction = Direction.HOLD
        rr = plan.net_rr if direction != Direction.HOLD else None
        if rr is not None and rr+1e-8 < float(self.cfg.get("trading.min_rr", 1.5)):
            reasons.append("Risk/reward below minimum"); direction = Direction.HOLD
        candle_time = frames["entry"].time.iloc[-1].to_pydatetime() if hasattr(frames["entry"].time.iloc[-1], "to_pydatetime") else datetime.now(timezone.utc)
        identity = "ensemble-v1" if self.cfg.get("strategy.family", "ensemble") in ("auto", "ensemble") else family
        profile = self.cfg.get("profile.name", "baseline")
        identity = (profile + ":" if profile != "baseline" else "") + identity
        sid = signal_id(symbol, self.cfg.get("strategy.timeframes.entry", "M5"), direction.value, candle_time,
            identity + ":" + self.revision)
        if direction == Direction.HOLD:
            sl = tp = rr = None
        return Signal(symbol=symbol, direction=direction, confidence=confidence, entry_price=entry, stop_loss=sl, take_profit=tp,
                      risk_reward=rr, market_regime=regime, trend_score=combined_trend, momentum_score=momentum_score,
                      volume_score=volume_score, structure_score=structure_score, price_action_score=pa["score"],
                      news_score=news_score, volatility_score=vol_score, spread_score=0.5 if spread_points is None else max(0.0, 1.0-min(spread_points/100,1)),
                      reasons=reasons, timeframe=self.cfg.get("strategy.timeframes.entry", "M5"), candle_time=candle_time, strategy=family, signal_id=sid,
                      meta={"profile":profile,"selection":selection.to_dict(),"candidate_plans":candidate_plans,
                            "indicators":indicator_snapshot(frames),"technical_score":technical_score,
                            "reason_codes":list(dict.fromkeys(codes)),"config_revision":self.revision,"score_type":"uncalibrated_rule_suitability",
                            "trade_plan":plan.to_dict(),"reward_risk_policy":self.cfg.get("strategy.reward_risk",{}),
                            "risk_policy":{"risk_per_trade":self.cfg.get("trading.risk_per_trade",.005),"max_total_risk":self.cfg.get("trading.max_total_risk",.01)}})
