from __future__ import annotations

from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score, log_loss, accuracy_score
from sklearn.model_selection import TimeSeriesSplit

from ml.model import ModelBundle, create_model


def train_time_series(X, y, kind: str = "logistic", n_splits: int = 5) -> ModelBundle:
    common = X.index.intersection(y.dropna().index); X=X.loc[common]; y=y.loc[common].astype(int)
    splitter=TimeSeriesSplit(n_splits=n_splits); fold_metrics=[]
    for tr, va in splitter.split(X):
        model=create_model(kind); model.fit(X.iloc[tr], y.iloc[tr]); p=model.predict_proba(X.iloc[va])[:,1]
        fold_metrics.append({"auc":float(roc_auc_score(y.iloc[va],p)) if len(set(y.iloc[va]))>1 else None,
                             "log_loss":float(log_loss(y.iloc[va],p,labels=[0,1])),"accuracy":float(accuracy_score(y.iloc[va],p>=.5))})
    final=create_model(kind); final.fit(X,y)
    return ModelBundle(final, list(X.columns), calibrated=False, metrics={"folds":fold_metrics})


def save_bundle(bundle: ModelBundle, path: str = "models/model.joblib") -> str:
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True); bundle.save(str(p)); return str(p)
