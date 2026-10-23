"""Compare closed-bar setups, with one representative per correlated rule group."""
from dataclasses import asdict, dataclass, field
import math
from statistics import mean

from signals.strategies import FAMILIES, GROUPS, strategy_direction
from utils.types import Direction, MarketRegime


@dataclass
class Assessment:
    strategy: str
    group: str
    direction: Direction
    score: float
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    components: dict = field(default_factory=dict)

    def to_dict(self):
        result = asdict(self)
        result["direction"] = self.direction.value
        return result


@dataclass
class Selection:
    direction: Direction
    strategy: str
    score: float
    reason: str
    groups: list[str]
    assessments: list[Assessment]

    def to_dict(self):
        return {"direction": self.direction.value, "strategy": self.strategy, "score": self.score,
                "reason": self.reason, "confirming_groups": self.groups,
                "score_type": "uncalibrated_rule_suitability",
                "assessments": [a.to_dict() for a in self.assessments]}


def _number(row, name):
    value = row.get(name)
    return float(value) if value is not None and math.isfinite(float(value)) else None


def indicator_snapshot(frames):
    groups = {"trend": ("ema_20", "ema_50", "ema_200", "sma_20", "adx", "adx_pos", "adx_neg", "tenkan", "kijun", "cloud_a", "cloud_b", "supertrend", "supertrend_direction"),
              "oscillators": ("rsi", "macd", "macd_signal", "macd_hist", "stoch_k", "stoch_d", "stoch_rsi_k", "stoch_rsi_d", "cci", "williams_r", "roc"),
              "volatility": ("atr", "bb_low", "bb_mid", "bb_high", "bb_width", "keltner_high", "keltner_low", "donchian_high", "donchian_low"),
              "volume": ("obv", "vwap", "relative_volume")}
    return {role: {"volume_source":str(df.iloc[-1].get("volume_source","unavailable")),
                   **{group: {name:_number(df.iloc[-1], name) for name in names} for group,names in groups.items()}}
            for role,df in frames.items() if len(df)}


def _oscillator_support(x, direction, reversion):
    # Related oscillators share ONE bounded component; they are not independent votes.
    signs = []
    for name, midpoint, scale in (("rsi",50,25), ("stoch_k",50,50), ("stoch_rsi_k",.5,.5), ("williams_r",-50,50), ("cci",0,200)):
        value = _number(x,name)
        if value is not None:
            signs.append(max(-1, min(1, (value-midpoint)/scale)))
    for name in ("macd_hist", "roc"):
        value = _number(x,name)
        if value is not None:
            signs.append(1 if value>0 else -1 if value<0 else 0)
    bias = mean(signs) if signs else 0
    if reversion:
        bias *= -1
    return .5 + .5 * bias * (1 if direction == Direction.BUY else -1)


def assess_strategies(frames, regime, cfg, structure):
    enabled = cfg.get("strategy.enabled_strategies", list(FAMILIES))
    results = []
    for family in FAMILIES:
        if family not in enabled:
            results.append(Assessment(family, GROUPS[family], Direction.HOLD, 0, False, ["Disabled by configuration"]))
            continue
        direction, _, reason = strategy_direction(frames, regime, family,
            int(cfg.get("strategy.breakout_lookback",20)),float(cfg.get("strategy.pullback_atr",.5)))
        if direction == Direction.HOLD:
            results.append(Assessment(family, GROUPS[family], direction, 0, False, [reason]))
            continue
        if (direction == Direction.BUY and structure.get("direction") == "BEARISH") or (direction == Direction.SELL and structure.get("direction") == "BULLISH"):
            results.append(Assessment(family, GROUPS[family], direction, 0, False, [reason,"Conflicting confirmed market structure"]))
            continue
        x = frames["entry"].iloc[-1]
        reversion = GROUPS[family] == "reversion"
        aligned = []
        for role in ("macro","trend"):
            row = frames[role].iloc[-1]
            bullish = row.ema_20 > row.ema_50 > row.ema_200
            bearish = row.ema_20 < row.ema_50 < row.ema_200
            side = 1 if bullish else -1 if bearish else 0
            aligned.append(.5 if side==0 else 1 if side==(1 if direction==Direction.BUY else -1) else 0)
        rv, adx = _number(x,"relative_volume"), _number(frames["trend"].iloc[-1],"adx")
        vol = .5 if rv is None else max(0,min(1,rv/1.5))
        atr_pct = float(x.atr/x.close)
        candle_range = max(float(x.high-x.low),1e-12)
        price = (float(x.close-x.open) / candle_range) * (1 if direction==Direction.BUY else -1)
        fit = .9 if reversion and regime==MarketRegime.RANGING else .85 if not reversion and regime!=MarketRegime.RANGING else .5
        if adx is not None:
            fit = (fit + (max(0,min(1,1-adx/40)) if reversion else max(0,min(1,adx/35))))/2
        components = {"regime_fit":fit,"higher_timeframes":mean(aligned),"oscillators":_oscillator_support(x,direction,reversion),
                      "volume":vol,"price_confirmation":.5+.5*price,"volatility":1 if .0005<=atr_pct<=.02 else .3,"entry_trigger":1.0}
        weights = cfg.get("strategy.selection.component_weights") or {"regime_fit":.25,"higher_timeframes":.20,"oscillators":.15,
                    "volume":.10,"price_confirmation":.10,"volatility":.10,"entry_trigger":.10}
        weight_sum = sum(weights.values())
        score = sum(components[k]*w for k,w in weights.items()) / weight_sum
        results.append(Assessment(family,GROUPS[family],direction,round(score,6),True,[reason],components))
    return results


def select_assessments(assessments, minimum_score=.65, minimum_groups=1):
    candidates = [a for a in assessments if a.eligible and a.direction != Direction.HOLD and math.isfinite(a.score) and a.score >= minimum_score]
    if not candidates:
        return Selection(Direction.HOLD,"ensemble",0,"No eligible setup meets the selection score",[],assessments)
    directions = {a.direction for a in candidates}
    if len(directions)>1:
        return Selection(Direction.HOLD,"ensemble",0,"Conflicting BUY/SELL setups; abstaining",[],assessments)
    representatives = {}
    for candidate in sorted(candidates,key=lambda a:(-a.score,a.strategy)):
        representatives.setdefault(candidate.group,candidate)
    if len(representatives)<minimum_groups:
        return Selection(Direction.HOLD,"ensemble",0,"Insufficient independent strategy groups",list(representatives),assessments)
    best = min(representatives.values(),key=lambda a:(-a.score,a.strategy))
    score = mean(a.score for a in representatives.values())
    return Selection(best.direction,best.strategy,score,"Selected highest suitability setup; correlated rules counted once",sorted(representatives),assessments)


def select_strategy(frames, regime, cfg, structure):
    assessments = assess_strategies(frames,regime,cfg,structure)
    family = cfg.get("strategy.family","ensemble")
    considered = assessments if family in ("auto","ensemble") else [a for a in assessments if a.strategy==family]
    result = select_assessments(considered,float(cfg.get("strategy.selection.minimum_score",.65)),
        int(cfg.get("strategy.selection.minimum_groups",1)) if family in ("auto","ensemble") else 1)
    result.assessments = assessments
    return result
