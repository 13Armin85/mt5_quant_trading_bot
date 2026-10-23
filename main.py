from __future__ import annotations

import argparse

from config import load_config
from core.data_feed import DataFeed
from core.execution import ExecutionEngine
from core.mt5_client import MT5Client
from core.risk_manager import RiskManager
from core.trading_engine import TradingEngine
from database.repository import Repository
from monitoring.logger import configure_logging
from news.news_filter import NewsFilter
from utils.types import TradingMode


def build_runtime():
    cfg=load_config(); configure_logging(); client=MT5Client(cfg.env); client.initialize()
    feed=DataFeed(client,int(cfg.get("trading.max_tick_age_seconds",15)))
    risk=RiskManager(cfg.get("trading",{}),client)
    repo=Repository(cfg.env.database_url)
    mode=TradingMode(str(cfg.get("trading.mode","PAPER")).upper())
    execution=ExecutionEngine(client,mode,cfg.env.enable_live_trading,cfg.get("trading.kill_switch_path","kill_switch.flag"))
    news=NewsFilter(cfg)
    return cfg,client,TradingEngine(cfg,client,feed,risk,execution,repo,news)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("command",choices=["run","health"]); args=parser.parse_args()
    cfg,client,engine=build_runtime()
    try:
        if args.command=="run": engine.run_forever()
        else: print({"mt5":client.health_check(),"mode":cfg.get("trading.mode")})
    finally: client.shutdown()

if __name__=="__main__": main()
