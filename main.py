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
    risk=RiskManager(cfg.get("trading",{}),client,mode=selected,symbols=cfg.symbols)
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
    doctor=commands.add_parser("doctor",help="Read-only clock, session, candle and spread diagnostics; never sends orders")
    doctor.add_argument("--symbol",action="append",help="Limit to a logical symbol; may be repeated")
    doctor.add_argument("--output",default="reports/runtime_diagnostics.json")
    funnel = commands.add_parser("funnel", help="Read stored unique setup/rejection counts without MT5")
    funnel.add_argument("--scope")
    funnel.add_argument("--output", default="reports/funnel.json")
    resume = commands.add_parser("resume-risk",help="Queue a reviewed loss-pause resume for the running engine; never sends orders directly")
    resume.add_argument("--scope",required=True,help="Exact account/mode scope shown in heartbeat")
    resume.add_argument("--reason",required=True,help="Operator explanation, stored in the journal")
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
    backtest.add_argument("--compare-experiments",action="store_true",help="Compare three fixed research hypotheses against their baseline")
    backtest.add_argument("--experiment",default="baseline",choices=["baseline","h1_context","balanced","breakout_retest","range_midpoint"])
    backtest.add_argument("--family", default="ensemble", choices=["auto", "ensemble", *FAMILIES])
    backtest.add_argument("--profile", default="baseline", choices=["baseline","scalping","intraday","combined","compare"],
                          help="Profile research; compare runs each profile and one shared chronological portfolio")
    backtest.add_argument("--spread", type=float, default=0, help="Bid/Ask spread in PRICE units, not points/pips")
    backtest.add_argument("--slippage", type=float, default=0, help="Adverse slippage per fill in PRICE units")
    backtest.add_argument("--commission", type=float, default=7, help="Round-trip account currency per lot")
    backtest.add_argument("--tick-size", type=float, help="Historical price tick size; must be verified independently")
    backtest.add_argument("--symbol-costs",help="JSON keyed by symbol: spread/slippage in price, commission/lot, volume_step/min/max, tick_size")
    backtest.add_argument("--volume-step", type=float, default=.01)
    backtest.add_argument("--min-volume", type=float, default=.01)
    backtest.add_argument("--max-volume", type=float, default=100)
    backtest.add_argument("--output", default="reports/backtest.json")
    args=parser.parse_args()
    if args.command=="doctor":
        from monitoring.diagnostics import inspect_runtime
        from backtest.report import save_report
        cfg=load_config();cfg.validate_runtime()
        client=create_mt5_client(cfg,trading=False)
        try:
            client.initialize()
            report=inspect_runtime(cfg,client,args.symbol)
            save_report(report,args.output)
            print(json.dumps({"read_only":True,"mt5_connected":report["mt5_connected"],
                "permissions":report["permissions"],"report":args.output},ensure_ascii=True))
        finally:client.shutdown()
        return
    if args.command == "resume-risk":
        import uuid
        if not args.reason.strip():
            parser.error("--reason must explain the operator review")
        cfg = load_config()
        repo = Repository(cfg.env.database_url)
        if repo.get_state(args.scope + ":risk") is None:
            parser.error("Unknown scope; use the heartbeat scope for the intended account")
        request = {"id":uuid.uuid4().hex,"reason":args.reason,"time":datetime.now(timezone.utc).isoformat()}
        repo.set_state(args.scope + ":resume_request",request)
        print(json.dumps({"status":"queued","scope":args.scope,"request_id":request["id"],
            "note":"Engine rechecks kill switch, equity, daily/weekly/drawdown and risk caps; see resume_result"}))
        return
    if args.command == "funnel":
        from backtest.report import save_report
        repo = Repository(load_config().env.database_url)
        report = {"counts":repo.decision_funnel(args.scope),
            "observed_waits":{k:v for k,v in repo.states(args.scope+":" if args.scope else "").items() if ":wait:" in k},
            "basis":"Distinct setup/config/candle per stage and reason; repeated polls do not add counts",
            "note":"A setup may pass through multiple stages/reasons; counts must not be summed as trades"}
        print(save_report(report,args.output))
        return
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
        from backtest.research import generate_signals, compare_strategies, compare_experiments
        from backtest.report import save_report
        if args.compare and args.compare_experiments:
            parser.error("Choose either --compare or --compare-experiments")
        if args.profile != "baseline" and (args.compare or args.compare_experiments or args.signals):
            parser.error("Profile research generates its own signals; omit --signals/--compare/--compare-experiments")
        cfg = load_config(); cfg.validate_runtime()
        cfg = cfg.model_copy(deep=True)
        cfg.settings["strategy"]["experiment"] = args.experiment
        bars = pd.read_csv(args.bars)
        bars["time"] = pd.to_datetime(bars.time, utc=True)
        kwargs = dict(spread=args.spread, slippage=args.slippage, commission_per_lot=args.commission,
                      volume_step=args.volume_step, volume_min=args.min_volume, volume_max=args.max_volume,
                      tick_size=args.tick_size,max_total_risk=float(cfg.get("trading.max_total_risk",.01)),
                      max_positions=int(cfg.get("trading.max_positions",3)),max_trades_per_day=int(cfg.get("trading.max_trades_per_day",10)),
                      risk_limits=cfg.get("trading",{}),position_management=cfg.get("position_management",{}),
                      symbols=cfg.symbols,
                      risk_fraction=float(cfg.get("trading.risk_per_trade",.005)),minimum_net_rr=max(float(cfg.get("trading.min_rr",1.5)),float(cfg.get("strategy.reward_risk.minimum_net_rr",1.5))))
        if args.symbol_costs:
            kwargs["symbol_costs"] = json.loads(Path(args.symbol_costs).read_text(encoding="utf-8"))
        if args.profile != "baseline":
            kwargs["profile_priority"] = cfg.get("profiles.priority", ["intraday","scalping"])
            kwargs["profile_configs"] = {name:{
                "max_open_positions":int(cfg.get(f"profiles.{name}.max_open_positions",1)),
                "position_management":cfg.for_profile(name).get("position_management",{})}
                for name in ("scalping","intraday")}
        if not args.signals and (args.contract_value is None or args.contract_value <= 0):
            parser.error("--contract-value is required without --signals; use broker account-currency value per one price unit/lot")
        if args.profile != "baseline":
            if args.contract_value is None or args.contract_value <= 0:
                parser.error("--contract-value is required for profile research")
            names = ([args.profile] if args.profile in {"scalping","intraday"} else ["scalping","intraday"])
            generated = {}
            errors = {}
            for name in names:
                try:
                    profile_cfg = cfg.for_profile(name)
                    profile_cfg.validate_runtime()
                    generated[name] = generate_signals(bars, profile_cfg, args.contract_value,
                        profile_cfg.get("strategy.family","ensemble"), costs=kwargs)
                except ValueError as exc:
                    errors[name] = str(exc)
            if not generated:
                raise ValueError("No profile has sufficient complete historical bars: " + str(errors))
            if args.profile == "combined" and errors:
                raise ValueError("Combined portfolio requires complete history for both profiles: " + str(errors))
            from backtest.provenance import manifest
            if args.profile == "compare" and errors:
                errors["combined"] = "Combined portfolio requires complete history for both profiles"
            scenarios = names + (["combined"] if args.profile in {"combined","compare"} and not errors else [])
            report = {"profiles":{},"errors":errors,"news_validated":False,
                "allocation":"fixed profile priority with per-profile capacity and shared portfolio risk",
                "limitations":["OHLC bars do not establish intrabar Bid/Ask or fill order", "Scalping needs broker-verified tick and cost history"],
                "manifest":manifest(bars,cfg,kwargs)}
            for name in scenarios:
                if name != "combined" and name not in generated:
                    continue
                selected = generated[name] if name != "combined" else pd.concat(generated.values(),ignore_index=True).sort_values(
                    ["time","profile"],kind="stable").reset_index(drop=True)
                test = BacktestEngine(**kwargs)
                trades, metrics = test.run(bars, selected)
                report["profiles"][name] = {"metrics":metrics,"rejections":test.rejections,
                    "signals":len(selected),"trades":len(trades)}
                Path(args.output).parent.mkdir(parents=True, exist_ok=True)
                trades.to_csv(Path(args.output).with_name(Path(args.output).stem+f".{name}.trades.csv"),index=False)
            print(save_report(report, args.output))
            return
        if args.compare or args.compare_experiments:
            if args.contract_value is None or args.contract_value <= 0:
                parser.error("--compare requires --contract-value")
            report = (compare_experiments if args.compare_experiments else compare_strategies)(bars, cfg, args.contract_value, kwargs)
        else:
            signals = pd.read_csv(args.signals) if args.signals else generate_signals(bars, cfg, args.contract_value, args.family,costs=kwargs)
            test = BacktestEngine(**kwargs)
            trades, metrics = test.run(bars, signals)
            report = {"metrics": metrics, "rejections": test.rejections, "news_validated": False, "costs": kwargs}
            from backtest.provenance import manifest
            report["manifest"] = manifest(bars,cfg,kwargs)
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            trades.to_csv(Path(args.output).with_suffix(".trades.csv"), index=False)
            test.equity_curve.to_csv(Path(args.output).with_suffix(".equity.csv"),index=False)
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
