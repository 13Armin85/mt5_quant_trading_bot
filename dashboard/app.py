from __future__ import annotations

import streamlit as st
from sqlalchemy import create_engine, text
from config import load_config

st.set_page_config(page_title="MT5 Quant Bot", layout="wide")
cfg=load_config(); st.title("MT5 Quant Trading Bot")
st.caption("Default safety mode: PAPER. Live trading requires explicit configuration and validation.")

c1,c2,c3,c4=st.columns(4)
c1.metric("Trading Mode", cfg.get("trading.mode","PAPER"))
c2.metric("Risk / Trade", f"{cfg.get('trading.risk_per_trade',.01)*100:.1f}%")
c3.metric("Min Confidence", f"{cfg.get('trading.min_confidence',.70)*100:.0f}%")
c4.metric("Max Daily Loss", f"{cfg.get('trading.max_daily_loss',.03)*100:.0f}%")

try:
    engine=create_engine(cfg.env.database_url)
    with engine.connect() as conn:
        rows=conn.execute(text("SELECT symbol,direction,confidence,market_regime,lot,status,timestamp,reason FROM trades ORDER BY id DESC LIMIT 100")).mappings().all()
    st.subheader("Recent Trades / Executions")
    st.dataframe(rows, use_container_width=True)
except Exception as exc:
    st.info(f"Database not ready yet: {exc}")

st.subheader("Safety")
st.write("LIVE mode remains blocked unless ENABLE_LIVE_TRADING=true and the runtime mode is LIVE. The kill_switch.flag file blocks new entries immediately.")
