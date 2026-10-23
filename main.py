from __future__ import annotations

import argparse
import hashlib
import json
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import load_config, ROOT
from core.data_feed import DataFeed
from core.execution import ExecutionEngine
from core.mt5_remote import create_mt5_client
from core.risk_manager import RiskManager
from core.trading_engine import TradingEngine
from database.repository import Repository
from monitoring.logger import configure_logging
from news.news_filter import NewsFilter
from news.background import ConcurrentNewsFilter
from utils.types import TradingMode
from core.symbol_resolver import SymbolResolver
from core.runtime_lock import RuntimeLock
from signals.strategies import FAMILIES


def build_runtime(mode=None):
    cfg=load_config()
    if mode:
        cfg.settings["trading"]["mode"] = mode.upper()
    cfg.validate_runtime()
    configure_logging()
    selected = TradingMode(str(cfg.get("trading.mode", "PAPER")).upper())
    if selected == TradingMode.BACKTEST:
        raise ValueError("Use main.py backtest --bars ... for offline backtesting")
    if selected == TradingMode.LIVE and not cfg.env.enable_live_trading:
        raise ValueError("LIVE blocked: set ENABLE_LIVE_TRADING=true explicitly")
    client=create_mt5_client(cfg, trading=selected == TradingMode.LIVE); client.initialize()
    feed=DataFeed(client,int(cfg.get("trading.max_tick_age_seconds",15)),cfg.symbols)
    risk=RiskManager(cfg.get("trading",{}),client)
    repo=Repository(cfg.env.database_url)
    account = client.account_info()
    identity = hashlib.sha256(f"{account.server}:{account.login}".encode()).hexdigest()[:16]
    scope = f"{selected.value}:{identity}:{int(cfg.get('trading.magic', 260912))}"
    execution=ExecutionEngine(client,selected,cfg.env.enable_live_trading,ROOT / (cfg.env.kill_switch_path or cfg.get("trading.kill_switch_path","kill_switch.flag")),risk,repo,cfg.get("trading",{}),scope)
    news=ConcurrentNewsFilter(cfg)
    return cfg,client,TradingEngine(cfg,client,feed,risk,execution,repo,news)


def main():
    parser=argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--mode", choices=["paper", "live"])
    commands.add_parser("health")
    news = commands.add_parser("news", help="Read-only news connection check; no MT5 or orders")
    news.add_argument("--symbol", default="XAUUSD")
    news.add_argument("--output", default="reports/news_status.json")
    symbols = commands.add_parser("symbols", help="List broker instruments without sending orders")
    symbols.add_argument("--contains", help="Filter instrument names, e.g. EURUSD")
    symbols.add_argument("--all", action="store_true", help="List every broker instrument")
    analyze = commands.add_parser("analyze", help="Read-only snapshot of all enabled strategies; never submits orders")
    analyze.add_argument("--symbol", required=True)
    analyze.add_argument("--output", default="reports/analysis.json")
    export = commands.add_parser("export-bars")
    export.add_argument("--symbol", required=True)
    export.add_argument("--days", type=int, default=90)
    export.add_argument("--output", default="reports/bars.csv")
    backtest = commands.add_parser("backtest")
    backtest.add_argument("--bars", required=True)
    backtest.add_argument("--signals")
    backtest.add_argument("--contract-value", type=float)
    backtest.add_argument("--compare", action="store_true")
    backtest.add_argument("--family", default="ensemble", choices=["auto", "ensemble", *FAMILIES])
    backtest.add_argument("--spread", type=float, default=0)
    backtest.add_argument("--slippage", type=float, default=0)
    backtest.add_argument("--commission", type=float, default=7)
    backtest.add_argument("--volume-step", type=float, default=.01)
    backtest.add_argument("--min-volume", type=float, default=.01)
    backtest.add_argument("--max-volume", type=float, default=100)
    backtest.add_argument("--output", default="reports/backtest.json")
    args=parser.parse_args()
    if args.command == "news":
        from dataclasses import asdict
        from backtest.report import save_report
        cfg = load_config(); cfg.validate_runtime()
        filt = NewsFilter(cfg)
        now = datetime.now(timezone.utc)
        allowed, reason, _ = filt.evaluate(args.symbol, now)
        currencies = filt.currencies_for(args.symbol)
        headlines = getattr(filt.headlines, "all_events", [])
        report = {"read_only": True, "orders_enabled": False, "time": now.isoformat(),
                  "symbol": args.symbol, "news_allows_entry": allowed, "reason": reason,
                  "sources": filt.status(),
                  "upcoming_events": [asdict(e) for e in filt.cache if e.kind == "release" and e.currency in currencies and e.timestamp >= now],
                  "latest_headlines": [asdict(e) for e in sorted(headlines, key=lambda e: e.timestamp, reverse=True) if e.currency in currencies][:20]}
        report = json.loads(json.dumps(report, default=str, ensure_ascii=False))
        save_report(report, args.output)
        print(json.dumps({**report, "report": args.output}, indent=2, ensure_ascii=True))
        return
    if args.command == "analyze":
        from analysis.scanner import scan_symbol
        from backtest.report import save_report
        cfg = load_config();cfg.validate_runtime()
        client = create_mt5_client(cfg)
        try:
            client.initialize()
            result = scan_symbol(cfg,client,args.symbol)
            save_report(result,args.output)
            print(json.dumps({"symbol":result["symbol"],"read_only":True,"regime":result["regime"],
                              "selection":result["selection"],"report":args.output},indent=2,ensure_ascii=False))
        finally:
            client.shutdown()
        return
    if args.command == "symbols":
        cfg = load_config()
        client = create_mt5_client(cfg)
        try:
            client.initialize()
            instruments = client.symbols_get()
            if instruments is None:
                raise ValueError("Broker symbol catalog unavailable")
            needles = [args.contains.upper()] if args.contains else [name.upper() for name in cfg.symbols.get("symbols", {})]
            rows = [{"name":s.name,"path":s.path,"currency_base":s.currency_base,"currency_profit":s.currency_profit,
                     "trade_mode":s.trade_mode,"visible":s.visible}
                    for s in instruments if args.all or any(needle in s.name.upper() for needle in needles)]
            print(json.dumps({"total_symbols":len(instruments),"symbols":rows},indent=2,ensure_ascii=False))
        finally:
            client.shutdown()
        return
    if args.command == "backtest":
        import pandas as pd
        from backtest.engine import BacktestEngine
        from backtest.research import generate_signals, compare_strategies
        from backtest.report import save_report
        cfg = load_config(); cfg.validate_runtime()
        bars = pd.read_csv(args.bars)
        bars["time"] = pd.to_datetime(bars.time, utc=True)
        kwargs = dict(spread=args.spread, slippage=args.slippage, commission_per_lot=args.commission,
                      volume_step=args.volume_step, volume_min=args.min_volume, volume_max=args.max_volume,
                      risk_fraction=float(cfg.get("trading.risk_per_trade",.005)),minimum_net_rr=max(float(cfg.get("trading.min_rr",1.5)),float(cfg.get("strategy.reward_risk.minimum_net_rr",1.5))))
        if not args.signals and (args.contract_value is None or args.contract_value <= 0):
            parser.error("--contract-value is required without --signals; use broker account-currency value per one price unit/lot")
        if args.compare:
            if args.contract_value is None or args.contract_value <= 0:
                parser.error("--compare requires --contract-value")
            report = compare_strategies(bars, cfg, args.contract_value, kwargs)
        else:
            signals = pd.read_csv(args.signals) if args.signals else generate_signals(bars, cfg, args.contract_value, args.family,costs=kwargs)
            test = BacktestEngine(**kwargs)
            trades, metrics = test.run(bars, signals)
            report = {"metrics": metrics, "rejections": test.rejections, "news_validated": False, "costs": kwargs}
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            trades.to_csv(Path(args.output).with_suffix(".trades.csv"), index=False)
        print(save_report(report, args.output))
        return
    if args.command == "run":
        with RuntimeLock(load_config().env.runtime_lock_path):
            cfg,client,engine=build_runtime(args.mode)
            previous = {sig: signal.signal(sig, lambda *_: engine.stop()) for sig in (signal.SIGINT, signal.SIGTERM)}
            try:
                engine.run_forever()
            finally:
                client.shutdown()
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
        return
    cfg,client,engine=build_runtime("paper")
    try:
        if args.command == "export-bars":
            import pandas as pd
            symbol = SymbolResolver(client, cfg.symbols).resolve(args.symbol)
            end = datetime.now(timezone.utc)
            rates = client.copy_rates_range(symbol, client.timeframe(cfg.get("strategy.timeframes.entry", "M5")), end - timedelta(days=args.days), end)
            if rates is None or len(rates) == 0:
                raise ValueError("MT5 returned no history; load history/increase Max bars in chart")
            bars = pd.DataFrame(rates)
            bars["time"] = pd.to_datetime(bars.time, unit="s", utc=True)
            seconds = {"M1":60,"M5":300,"M15":900,"M30":1800,"H1":3600,"H4":14400,"D1":86400}[cfg.get("strategy.timeframes.entry", "M5")]
            bars = bars[bars.time + pd.Timedelta(seconds=seconds) <= pd.Timestamp(end)]
            DataFeed.validate_bars(bars)
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            bars.to_csv(args.output, index=False)
            print(json.dumps({"bars":len(bars),"first":str(bars.time.iloc[0]),"last":str(bars.time.iloc[-1]),"path":args.output}))
        else:
            account, terminal = client.account_info(), client.terminal_info()
            status = {"mt5":client.health_check(), "mode":cfg.get("trading.mode"), "account":f"***{str(account.login)[-4:]}",
                      "currency":account.currency,"equity":account.equity,"free_margin":account.margin_free,
                      "market_time_zone":client.market_timezone.key,
                      "permissions":{"terminal_algo_trading":bool(terminal.trade_allowed),"external_python_disabled":bool(terminal.tradeapi_disabled),"account_trade_allowed":bool(account.trade_allowed),"account_trade_expert":bool(account.trade_expert)},
                      "account_mode":"HEDGING" if int(account.margin_mode)==2 else "NETTING",
                      "trade_allowed":bool(terminal.trade_allowed and account.trade_allowed and account.trade_expert and not terminal.tradeapi_disabled),"symbols":{}}
            for logical in cfg.symbols["symbols"]:
                try:
                    symbol = engine.resolver.resolve(logical)
                    info = client.symbol_info(symbol)
                    news_ok, reason, _ = engine.news_filter.evaluate(logical)
                    tick = client.symbol_info_tick(symbol)
                    tick_time = datetime.fromtimestamp(tick.time, timezone.utc) if tick else None
                    quote_status = engine.feed.tick_status(symbol)
                    status["symbols"][logical] = {"broker_symbol":symbol,"fresh_tick":quote_status["fresh"],"quote_status":quote_status,"last_tick_utc":tick_time.isoformat() if tick_time else None,"tick_age_seconds":round((datetime.now(timezone.utc)-tick_time).total_seconds(),1) if tick_time else None,"spread_points":engine.feed.spread_points(symbol),"news_ok":news_ok,"news_reason":reason,
                        "specification":{k:getattr(info,k,None) for k in ("trade_contract_size","trade_tick_size","trade_tick_value","volume_min","volume_max","volume_step","trade_stops_level","trade_freeze_level","filling_mode")}}
                except ValueError as exc:
                    status["symbols"][logical] = {"error":str(exc)}
            status["news_sources"] = engine.news_filter.status()
            print(json.dumps(status, indent=2, ensure_ascii=False))
    finally: client.shutdown()

if __name__=="__main__": main()
