from __future__ import annotations

from dataclasses import dataclass
import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier


@dataclass
class ModelBundle:
    model: object
    feature_names: list[str]
    calibrated: bool = False
    metrics: dict | None = None

    def predict_proba(self, X):
        return self.model.predict_proba(X)[:,1]

    def save(self, path: str):
        joblib.dump(self, path)

    @staticmethod
    def load(path: str):
        return joblib.load(path)


def create_model(kind: str = "logistic", random_state: int = 42):
    if kind == "logistic": return LogisticRegression(max_iter=2000, class_weight="balanced", random_state=random_state)
    if kind == "random_forest": return RandomForestClassifier(n_estimators=400, max_depth=7, min_samples_leaf=8, class_weight="balanced", n_jobs=-1, random_state=random_state)
    if kind == "xgboost": return XGBClassifier(n_estimators=400, max_depth=4, learning_rate=.03, subsample=.8, colsample_bytree=.8, eval_metric="logloss", random_state=random_state)
    raise ValueError(f"Unknown model kind: {kind}")
