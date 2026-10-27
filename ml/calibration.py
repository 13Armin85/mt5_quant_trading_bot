from __future__ import annotations

import numpy as np
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.frozen import FrozenEstimator


def calibrate_prefit(model, X_cal, y_cal, method: str = "isotonic", *, X_eval=None, y_eval=None):
    if X_eval is None or y_eval is None:
        raise ValueError("Independent chronological evaluation data is required")
    if len(X_cal.index.intersection(X_eval.index)) or X_cal.index.max() >= X_eval.index.min():
        raise ValueError("Calibration and evaluation must be disjoint and chronological")
    calibrated = CalibratedClassifierCV(FrozenEstimator(model), method=method)
    calibrated.fit(X_cal, y_cal)
    p = calibrated.predict_proba(X_eval)[:,1]
    true, pred = calibration_curve(y_eval, p, n_bins=10, strategy="quantile")
    counts, _ = np.histogram(p,bins=np.linspace(0,1,11))
    return calibrated, {"brier": float(brier_score_loss(y_eval, p)),"log_loss":float(log_loss(y_eval,p,labels=[0,1])),
        "evaluation_count":len(p),"uniform_bucket_counts":counts.tolist(),"curve_basis":"quantile buckets",
        "curve_true": true.tolist(), "curve_pred": pred.tolist(),"basis":"Independent evaluation; model training must precede calibration"}


def confidence_bucket_accuracy(y, p, low=.65, high=.75) -> dict:
    p=np.asarray(p); y=np.asarray(y); mask=(p>=low)&(p<=high)
    return {"count":int(mask.sum()),"empirical_win_rate":float(y[mask].mean()) if mask.any() else None,"mean_predicted":float(p[mask].mean()) if mask.any() else None}
