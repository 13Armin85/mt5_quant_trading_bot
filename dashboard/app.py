from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import streamlit as st

from config import load_config
from monitoring.runtime_view import read_runtime
from dashboard.login_view import authenticate, account_header, portal_client
from dashboard.account_client import PortalUnavailable


st.set_page_config(page_title="MT5 Quant Bot", layout="wide")
cfg = load_config()
account_status = authenticate(cfg)
account_header(cfg, account_status)
st.title("MT5 Quant Trading Bot")
st.caption("داشبورد معاملات · به‌روزرسانی هر ۵ ثانیه · تحلیل بازار و اخبار در کنار مدیریت موقعیت‌ها")


@st.fragment(run_every=5)
def render():
    try:
        status = portal_client(cfg).request("/status", session=st.session_state.get("account_session"))
        if not status.get("ok"):
            st.session_state.pop("account_session", None)
            st.rerun()
        if not status.get("connected"):
            st.warning("اتصال متاتریدر در دسترس نیست. برای اتصال مجدد، از منوی مدیریت حساب خارج و دوباره وارد شوید.")
    except PortalUnavailable as exc:
        st.error(str(exc))
        return
    try:
        view = read_runtime(cfg.env.database_url, scope=status.get("scope"), since=status.get("started_at"))
    except (OSError, sqlite3.Error, ValueError):
        st.info("موتور در حال آماده‌سازی ژورنال است؛ وضعیت چند ثانیهٔ دیگر به‌روز می‌شود.")
        return
    heartbeat = view["heartbeat"] or {}
    if heartbeat.get("phase") == "starting":
        st.info("حساب متصل است؛ موتور در حال آماده‌سازی داده‌های بازار و تحلیل اولیه است.")
    clocks=heartbeat.get("clocks") or {}
    if clocks:
        st.caption(f"UTC: {clocks.get('utc','')} · Server: {clocks.get('server','')} · Tehran: {clocks.get('tehran','')}")
    age = ((datetime.now(timezone.utc) - datetime.fromisoformat(heartbeat["time"])).total_seconds()
           if heartbeat.get("time") else None)
    c1,c2,c3,c4 = st.columns(4)
    mode = heartbeat.get("mode", "Not running")
    c1.metric("Runtime mode", mode if age is not None and age < 180 else "Stopped · " + mode)
    base_risk = float(cfg.get("trading.risk_per_trade", .005))
    quality_risk = cfg.get("trading.quality_sizing", {})
    upper_risk = float(quality_risk.get("risk_per_trade", base_risk)) if quality_risk.get("enabled") else base_risk
    c2.metric("Risk / trade (base / strong)", f"{base_risk*100:.1f}% / {upper_risk*100:.1f}%")
    c3.metric("Account connected", "Yes" if heartbeat.get("connected") and age is not None and age < 180 else "No")
    c4.metric("Heartbeat age", f"{age:.0f}s" if age is not None else "Waiting")
    if age is None or age > 180:
        st.warning("The engine is stopped or its heartbeat is stale. The information below is its last saved state.")
    if heartbeat.get("risk_state"):
        risk_state=heartbeat["risk_state"]
        st.write("Risk status:",risk_state.get("status","ACTIVE"),"· Resume:",risk_state.get("resume_at") or "Operator review")
    news = heartbeat.get("news", {})
    st.subheader("News and economic calendar")
    st.write({"background_running":news.get("background_running",False),
              "updating":news.get("refresh_in_progress",False),"snapshot_age_seconds":news.get("snapshot_age_seconds"),
              "last_success":news.get("last_success"),"error":news.get("last_error") or news.get("worker_error")})
    if news.get("headlines"):
        st.dataframe(news["headlines"], width="stretch")
    left,right = st.columns(2)
    with left:
        st.caption("Upcoming economic events")
        st.dataframe(news.get("upcoming_events",[]), width="stretch")
    with right:
        st.caption("Latest publisher headlines (publication time shown)")
        st.dataframe(news.get("latest_headlines",[]), width="stretch")
    st.subheader("Chart analysis and strategy selection")
    if heartbeat.get("quotes"):
        st.dataframe(list(heartbeat["quotes"].values()), width="stretch")
    if not view["analyses"]:
        st.info("Chart analysis appears when fresh broker quotes and completed candles are available. Check the quote status when the market is closed.")
    for analysis in view["analyses"]:
        if not isinstance(analysis, dict):
            continue
        st.write(analysis["symbol"], analysis.get("profile", "baseline"), analysis.get("strategy"), analysis.get("direction"))
        if analysis.get("reason"):
            st.caption(analysis["reason"])
        st.caption(f"Analyzed: {analysis.get('updated_at','')} · {analysis.get('news',{}).get('reason','')}")
        chart = analysis.get("live_chart") or {}
        if chart:
            st.caption(f"MT5 chart: {chart.get('status')} · Bid: {chart.get('bid')} · Ask: {chart.get('ask')} · Tick: {chart.get('tick_utc')}")
            if chart.get("forming_bar"):
                with st.expander("Current forming bar and provisional technical levels · " + analysis["symbol"]):
                    st.json({"forming_bar": chart["forming_bar"],
                        "provisional_indicators": chart.get("provisional_indicators", {}),
                        "note": "Confirmed strategies use completed candles; these live levels can change before close."})
        selection = analysis.get("selection") or {}
        st.caption(selection.get("reason", ""))
        sizing = analysis.get("sizing") or {}
        if sizing:
            st.caption(f"Risk tier: {sizing.get('tier')} | Per-trade risk ceiling: {float(sizing.get('risk_fraction', 0))*100:.2f}% of equity")
        if analysis.get("candidate_plans"):
            with st.expander("Candidate trade plans"):
                st.dataframe([{key: value for key, value in plan.items() if key != "trade_plan"}
                    for plan in analysis["candidate_plans"]], width="stretch")
        st.dataframe(selection.get("assessments",[]), width="stretch")
        with st.expander("Indicators and oscillators · " + analysis["symbol"]):
            st.json(analysis.get("indicators"))
    st.subheader("Open journal positions by profile")
    st.dataframe(view.get("positions", []), width="stretch")
    st.subheader("Recent trades / executions")
    st.dataframe(view["trades"], width="stretch")
    with st.expander("Runtime details"):
        st.json(heartbeat)


render()
