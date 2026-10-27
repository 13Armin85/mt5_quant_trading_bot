from __future__ import annotations

from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score, log_loss, accuracy_score, brier_score_loss
from sklearn.model_selection import TimeSeriesSplit

from ml.model import ModelBundle, create_model


def train_time_series(X, y, kind: str = "logistic", n_splits: int = 5, *, label_start=None, label_end=None) -> ModelBundle:
    label_start = label_start if label_start is not None else y.attrs.get("label_start")
    label_end = label_end if label_end is not None else y.attrs.get("label_end")
    if label_start is None or label_end is None:
        raise ValueError("Provide actual label_start/label_end timestamps to purge overlapping events")
    common = X.index.intersection(y.dropna().index); X=X.loc[common]; y=y.loc[common].astype(int)
    starts, ends = label_start.loc[common], label_end.loc[common]
    if starts.isna().any() or ends.isna().any() or not starts.is_monotonic_increasing or (ends<starts).any():
        raise ValueError("Invalid chronological label intervals")
    splitter=TimeSeriesSplit(n_splits=n_splits); fold_metrics=[]
    for tr, va in splitter.split(X):
        # Timestamp purging remains valid after irregular feature/label filtering.
        tr = tr[(ends.iloc[tr] < starts.iloc[va].min()).to_numpy()]
        if len(tr)==0 or y.iloc[tr].nunique()<2:
            raise ValueError("Insufficient purged training data/classes")
        model=create_model(kind); model.fit(X.iloc[tr], y.iloc[tr]); p=model.predict_proba(X.iloc[va])[:,1]
        fold_metrics.append({"auc":float(roc_auc_score(y.iloc[va],p)) if len(set(y.iloc[va]))>1 else None,
                             "log_loss":float(log_loss(y.iloc[va],p,labels=[0,1])),"accuracy":float(accuracy_score(y.iloc[va],p>=.5)),
                             "brier":float(brier_score_loss(y.iloc[va],p)),"train_count":len(tr),"validation_count":len(va),
                             "train_label_end":str(ends.iloc[tr].max()),"validation_start":str(starts.iloc[va].min())})
    final=create_model(kind); final.fit(X,y)
    return ModelBundle(final, list(X.columns), calibrated=False, metrics={"folds":fold_metrics})


def save_bundle(bundle: ModelBundle, path: str = "models/model.joblib") -> str:
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); bundle.save(str(p)); return str(p)
