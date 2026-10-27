"""Offline comparison on exported broker bars; never imports a trading runtime."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import time

import pandas as pd

from config import load_config, ROOT
from backtest.engine import BacktestEngine
from backtest.research import generate_signals
from backtest.report import save_report
from backtest.provenance import manifest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--bars",required=True)
    parser.add_argument("--specification",required=True,help="JSON of explicit historical cost assumptions")
    parser.add_argument("--max-bars",type=int,default=12000)
    parser.add_argument("--baseline-ref",default="3131ef6b036f598d1d2c224836746b5f32d0ecb5")
    parser.add_argument("--output",default="reports/engineering_comparison.json")
    args=parser.parse_args()
    cfg=load_config().model_copy(deep=True)
    cfg.settings["strategy"]["experiment"]="baseline"
    bars=pd.read_csv(args.bars).tail(args.max_bars).reset_index(drop=True)
    bars["time"]=pd.to_datetime(bars.time,utc=True)
    specification=json.loads(Path(args.specification).read_text(encoding="utf-8"))
    value=float(specification["contract_value_per_price"])
    kwargs={key:specification[key] for key in ("spread","slippage","commission_per_lot","volume_min","volume_max","volume_step")}
    kwargs.update(risk_fraction=cfg.get("trading.risk_per_trade",.005),minimum_net_rr=cfg.get("trading.min_rr",1.5),tick_size=specification["tick_size"])
    started=time.monotonic()
    signals=generate_signals(bars,cfg,value,"ensemble",costs=kwargs)
    signal_path=Path(args.output).with_suffix(".signals.csv")
    signal_path.parent.mkdir(parents=True,exist_ok=True)
    signals.to_csv(signal_path,index=False)
    print(json.dumps({"stage":"signals","count":len(signals),"seconds":round(time.monotonic()-started,1)}),flush=True)
    # Load the repository's committed baseline without changing the worktree.
    baseline=subprocess.run(["git","-c",f"safe.directory={ROOT.as_posix()}","show",f"{args.baseline_ref}:backtest/engine.py"],cwd=ROOT,capture_output=True,text=True,check=True).stdout
    baseline_path=Path(args.output).with_suffix(".baseline.py")
    baseline_path.write_text(baseline,encoding="utf-8")
    spec=importlib.util.spec_from_file_location("research_baseline",baseline_path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    report={"manifest":manifest(bars,cfg,kwargs),"specification":specification,
        "baseline_ref":args.baseline_ref,
        "comparison_basis":"Identical current baseline-rule signals; execution changes isolated; committed legacy simulator vs new simulator",
        "financial_conclusion":"Unevaluated edge: limited sample and historical costs/news unverified; no LIVE promotion",
        "variants":{}}
    variants={"legacy":module.BacktestEngine(**kwargs),
        "new_static":BacktestEngine(**kwargs,max_total_risk=cfg.get("trading.max_total_risk",.01)),
        "new_managed":BacktestEngine(**kwargs,max_total_risk=cfg.get("trading.max_total_risk",.01),
            position_management=cfg.get("position_management",{}),risk_limits=cfg.get("trading",{}))}
    for name,engine in variants.items():
        trades,metrics=engine.run(bars,signals)
        report["variants"][name]={"metrics":metrics,"rejected":len(engine.rejections)}
        from backtest.monte_carlo import simulate
        report["variants"][name]["uncertainty"] = simulate(trades.profit/engine.initial_equity if not trades.empty else [])
        trades.to_csv(Path(args.output).with_suffix("."+name+".trades.csv"),index=False)
    print(save_report(report,args.output),flush=True)


if __name__=="__main__":main()
