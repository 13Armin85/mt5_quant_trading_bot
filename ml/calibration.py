from __future__ import annotations

import numpy as np
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.metrics import brier_score_loss


def calibrate_prefit(model, X_cal, y_cal, method: str = "isotonic"):
    calibrated = CalibratedClassifierCV(model, method=method, cv="prefit")
    calibrated.fit(X_cal, y_cal)
    p = calibrated.predict_proba(X_cal)[:,1]
    true, pred = calibration_curve(y_cal, p, n_bins=10, strategy="quantile")
    return calibrated, {"brier": float(brier_score_loss(y_cal, p)), "curve_true": true.tolist(), "curve_pred": pred.tolist()}


def confidence_bucket_accuracy(y, p, low=.65, high=.75) -> dict:
    p=np.asarray(p); y=np.asarray(y); mask=(p>=low)&(p<=high)
    return {"count":int(mask.sum()),"empirical_win_rate":float(y[mask].mean()) if mask.any() else None,"mean_predicted":float(p[mask].mean()) if mask.any() else None}
