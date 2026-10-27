"""Read-only clock, quote, session and execution-gate diagnostics. Never send orders."""
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import sqlite3

from sqlalchemy.engine import make_url
from core.data_feed import DataFeed
from core.symbol_policy import trading_policy
from core.symbol_resolver import SymbolResolver


def inspect_runtime(cfg,client,symbols=None):
    now=datetime.now(timezone.utc)
    zone=getattr(client,"market_timezone",ZoneInfo(cfg.get("trading.mt5_time_zone","UTC")))
    feed=DataFeed(client,int(cfg.get("trading.max_tick_age_seconds",15)),cfg.symbols)
    resolver=SymbolResolver(client,cfg.symbols)
    account,terminal=client.account_info(),client.terminal_info()
    permissions={"terminal_algo_trading":bool(getattr(terminal,"trade_allowed",False)),
        "external_python_allowed":not bool(getattr(terminal,"tradeapi_disabled",True)),
        "account_trade_allowed":bool(getattr(account,"trade_allowed",False)),
        "account_trade_expert":bool(getattr(account,"trade_expert",False))}
    report={"read_only":True,"orders_enabled":False,"mt5_connected":client.health_check(),
        "live_authorized":cfg.env.enable_live_trading,"permissions":permissions,
        "clocks":{"utc":now.isoformat(),"server":now.astimezone(zone).isoformat(),
                  "tehran":now.astimezone(ZoneInfo("Asia/Tehran")).isoformat()},"symbols":{}}
    for logical in symbols or cfg.symbols["symbols"]:
        try:
            symbol=resolver.resolve(logical)
            quote=feed.tick_status(symbol)
            item={"broker_symbol":symbol,"quote":quote,"bars":{}}
            historical=[]
            if not quote["scheduled_closed"]:
                for role,tf in cfg.get("strategy.timeframes",{}).items():
                    try:
                        bars=feed.bars(symbol,tf,int(cfg.get("trading.default_bars",700)),allow_session_gaps=role!="entry")
                        if bars.empty:
                            raise ValueError("No completed history returned")
                        item["bars"][role]={"valid":True,"timeframe":tf,"opened_at_utc":bars.time.iloc[-1].isoformat(),
                            "closed_at_utc":bars.attrs["last_bar_closed_at"],"freshness_basis":bars.attrs["freshness_basis"]}
                        if role=="entry":
                            historical=list(bars.get("spread",[]))[-100:]
                    except (ValueError,RuntimeError,TimeoutError,ConnectionError) as exc:
                        item["bars"][role]={"valid":False,"timeframe":tf,"reason":str(exc)}
            else:
                item["bar_check"]="Deferred until configured session reopens; fresh quotes remain mandatory"
            policy=trading_policy(symbol,cfg.get("trading",{}),cfg.symbols)
            item["spread"]=feed.spread_status(symbol,historical,float(cfg.get("strategy.filters.max_spread_multiplier",2)),
                float(policy.get("max_spread_points",40)))
            report["symbols"][logical]=item
        except (ValueError,RuntimeError,TimeoutError,ConnectionError) as exc:
            report["symbols"][logical]={"reason":str(exc)}
    # Inspect the existing journal without creating tables or exposing balances/credentials.
    url=make_url(cfg.env.database_url)
    if url.drivername=="sqlite" and url.database and url.database!=":memory:" and Path(url.database).exists():
        with sqlite3.connect(Path(url.database).resolve().as_uri()+"?mode=ro",uri=True,timeout=10) as db:
            try:
                row=db.execute("SELECT payload FROM runtime_state WHERE key='heartbeat'").fetchone()
            except sqlite3.OperationalError:
                row=None
        if row:
            heartbeat=json.loads(row[0]);news=heartbeat.get("news",{})
            decision=heartbeat.get("last_decision",{})
            report["running_engine"]={"mode":heartbeat.get("mode"),"connected":heartbeat.get("connected"),
                "heartbeat_time":heartbeat.get("time"),"risk_status":heartbeat.get("risk_state",{}).get("status"),
                "news_calendar_ok":news.get("calendar_ok"),"news_worker_error":news.get("worker_error"),
                "last_decision":{k:decision.get(k) for k in ("symbol","direction","reason_code","reason")} if isinstance(decision,dict) else decision}
    return report
