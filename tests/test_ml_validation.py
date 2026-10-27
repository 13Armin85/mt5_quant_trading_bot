import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from ml.features import make_tp_before_sl_target
from ml.training import train_time_series
from ml.calibration import calibrate_prefit


def label_bars():
    return pd.DataFrame({"time":pd.date_range("2026-01-01",periods=10,freq="h",tz="UTC"),
        "open":[100.]*10,"high":[100.5]*10,"low":[99.5]*10,"close":[100.]*10,"atr":[1.]*10})


def test_timeout_excluded_instead_of_silently_labelled_loss():
    target=make_tp_before_sl_target(label_bars(),horizon=2)
    assert target.isna().all()
    assert target.attrs["label_end"].notna().any()


@pytest.mark.parametrize("direction",["BUY","SELL"])
def test_buy_sell_labels_use_next_open_and_sl_first_collision(direction):
    bars=label_bars();bars.loc[1,["high","low"]]=[105.,95.]
    target=make_tp_before_sl_target(bars,horizon=2,direction=direction)
    assert target.iloc[0]==0
    assert target.attrs["label_start"].iloc[0]==bars.time.iloc[1]
    assert target.attrs["label_end"].iloc[0]==bars.time.iloc[2]


def test_irregularly_filtered_labels_are_purged_by_event_time():
    index=pd.Index(np.arange(250)*3)
    X=pd.DataFrame({"x":np.sin(np.arange(250)),"y":np.cos(np.arange(250))},index=index)
    y=pd.Series(np.arange(250)%2,index=index)
    starts=pd.Series(pd.date_range("2026-01-01",periods=250,freq="h",tz="UTC"),index=index)
    ends=starts+pd.Timedelta(hours=12)
    bundle=train_time_series(X,y,n_splits=3,label_start=starts,label_end=ends)
    for fold in bundle.metrics["folds"]:
        assert pd.Timestamp(fold["train_label_end"])<pd.Timestamp(fold["validation_start"])
        assert "brier" in fold
    with pytest.raises(ValueError,match="actual label"):
        train_time_series(X,y)


def test_calibration_requires_independent_later_evaluation():
    X=pd.DataFrame({"x":np.sin(np.arange(100))},index=np.arange(100))
    y=pd.Series(np.arange(100)%2,index=X.index)
    model=LogisticRegression().fit(X.iloc[:40],y.iloc[:40])
    with pytest.raises(ValueError,match="Independent"):
        calibrate_prefit(model,X.iloc[40:70],y.iloc[40:70])
    with pytest.raises(ValueError,match="disjoint"):
        calibrate_prefit(model,X.iloc[40:70],y.iloc[40:70],X_eval=X.iloc[50:80],y_eval=y.iloc[50:80])
    _,metrics=calibrate_prefit(model,X.iloc[40:70],y.iloc[40:70],X_eval=X.iloc[70:],y_eval=y.iloc[70:])
    assert metrics["evaluation_count"]==30 and sum(metrics["uniform_bucket_counts"])==30
