from __future__ import annotations


def validate_for_live(cfg, client, feed, symbols: list[str], backtest_metrics: dict | None = None, model_valid: bool = True, news_valid: bool = True) -> tuple[bool,list[str]]:
    reasons=[]
    if not backtest_metrics:
        reasons.append("No out-of-sample research evidence supplied")
    elif backtest_metrics.get("cost_status") != "verified" or not backtest_metrics.get("news_validated",False):
        reasons.append("Historical costs/news are unverified; operational permission is separate from research readiness")
    if not cfg.env.enable_live_trading: reasons.append("ENABLE_LIVE_TRADING=false")
    if not client.health_check(): reasons.append("MT5 connection unhealthy")
    for s in symbols:
        if not feed.is_tick_fresh(s): reasons.append(f"Stale/missing tick: {s}")
        info=client.symbol_info(s)
        if info is None or not getattr(info,"trade_mode",0): reasons.append(f"Symbol not tradable/unknown: {s}")
    if not model_valid: reasons.append("Model/calibration invalid")
    if not news_valid and cfg.get("news.enabled",True): reasons.append("News data invalid")
    if backtest_metrics:
        v=cfg.get("validation",{})
        if backtest_metrics.get("total_trades",0)<int(v.get("minimum_trades",200)): reasons.append("Insufficient validation trades")
        if backtest_metrics.get("profit_factor",0)<float(v.get("minimum_profit_factor",1.3)): reasons.append("Profit factor below threshold")
        if backtest_metrics.get("expectancy",0)<=0 and v.get("require_positive_expectancy",True): reasons.append("Expectancy is not positive")
        if backtest_metrics.get("max_drawdown",1)>float(v.get("maximum_drawdown",.25)): reasons.append("Drawdown above threshold")
    return not reasons,reasons
