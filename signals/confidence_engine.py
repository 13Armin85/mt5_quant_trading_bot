from __future__ import annotations
import math


class ConfidenceEngine:
    def __init__(self, weights: dict[str, float]) -> None:
        self.weights = weights
        total = sum(weights.values())
        if not math.isfinite(total) or total <= 0 or any(v < 0 for v in weights.values()):
            raise ValueError("Confidence weights must be finite, nonnegative and sum to > 0")
        self.weights = {k: v / total for k, v in weights.items()}

    def score(self, components: dict[str, float], direction: str) -> float:
        if direction not in {"BUY", "SELL"}:
            return 0.0
        value = 0.0
        for name, weight in self.weights.items():
            if not math.isfinite(float(components.get(name, .5))):
                raise ValueError(f"Invalid confidence component: {name}")
            raw = max(0.0, min(1.0, float(components.get(name, 0.5))))
            directional = raw if direction == "BUY" or name in {"volatility", "news", "volume"} else 1.0 - raw
            value += weight * directional
        return max(0.0, min(1.0, value))
