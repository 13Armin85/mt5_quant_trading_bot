import json
import subprocess
import sys
from pathlib import Path
import pandas as pd


def test_offline_backtest_cli_writes_real_computed_report(tmp_path):
    # Synthetic fixture only, never a reported broker backtest.
    bars=pd.DataFrame({"time":pd.date_range("2026-01-01",periods=3,freq="h",tz="UTC"),
                       "open":[100,100,102],"high":[100,103,103],"low":[100,99.5,101],"close":[100,102,102]})
    signals=pd.DataFrame([{"time":bars.time.iloc[0],"direction":"BUY","entry":100,"sl":99,"tp":102,"contract_value_per_price":1,"volume":1}])
    bar_path,signal_path,output=tmp_path/"bars.csv",tmp_path/"signals.csv",tmp_path/"report.json"
    bars.to_csv(bar_path,index=False);signals.to_csv(signal_path,index=False)
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,str(root/"main.py"),"backtest","--bars",str(bar_path),"--signals",str(signal_path),"--commission","0","--output",str(output)],cwd=root,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    report=json.loads(output.read_text(encoding="utf-8"))
    assert report["metrics"]["total_trades"]==1 and report["metrics"]["net_profit"]==2
    assert report["metrics"]["profit_factor"] is None
    assert not report["news_validated"] and output.with_suffix(".trades.csv").exists()
