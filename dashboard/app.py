from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import streamlit as st

from config import load_config
from monitoring.runtime_view import read_runtime


st.set_page_config(page_title="MT5 Quant Bot", layout="wide")
cfg = load_config()
st.title("MT5 Quant Trading Bot")
st.caption("Read-only monitor · refreshes every 5 seconds · chart analysis and news run together in the trading engine")


@st.fragment(run_every=5)
def render():
    try:
        view = read_runtime(cfg.env.database_url)
    except (OSError, sqlite3.Error, ValueError):
        st.info("Waiting for the trading engine journal. Start the LIVE engine with Start-Live.cmd on Windows.")
        return
    heartbeat = view["heartbeat"] or {}
    age = ((datetime.now(timezone.utc) - datetime.fromisoformat(heartbeat["time"])).total_seconds()
           if heartbeat.get("time") else None)
    c1,c2,c3,c4 = st.columns(4)
    mode = heartbeat.get("mode", "Not running")
    c1.metric("Runtime mode", mode if age is not None and age < 180 else "Stopped · " + mode)
    c2.metric("Risk / trade", f"{cfg.get('trading.risk_per_trade',.005)*100:.1f}%")
    c3.metric("Account connected", "Yes" if heartbeat.get("connected") and age is not None and age < 180 else "No")
    c4.metric("Heartbeat age", f"{age:.0f}s" if age is not None else "Waiting")
    if age is None or age > 180:
        st.warning("The engine is stopped or its heartbeat is stale. The information below is its last saved state.")
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
        st.write(analysis["symbol"], analysis.get("strategy"), analysis.get("direction"))
        st.caption(f"Analyzed: {analysis.get('updated_at','')} · {analysis.get('news',{}).get('reason','')}")
        selection = analysis.get("selection") or {}
        st.caption(selection.get("reason", ""))
        st.dataframe(selection.get("assessments",[]), width="stretch")
        with st.expander("Indicators and oscillators · " + analysis["symbol"]):
            st.json(analysis.get("indicators"))
    st.subheader("Recent trades / executions")
    st.dataframe(view["trades"], width="stretch")
    with st.expander("Runtime details"):
        st.json(heartbeat)


render()
