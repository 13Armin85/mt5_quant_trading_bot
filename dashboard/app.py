from __future__ import annotations

import sqlite3
import os

import streamlit as st

from config import load_config
from dashboard.account_client import PortalUnavailable
from dashboard.login_view import authenticate, account_header, portal_client
from dashboard.theme import ASSETS, apply_theme
from dashboard.workspace import render_workspace
from monitoring.runtime_view import read_runtime


st.set_page_config(page_title="نواتک | میزکار معاملات", page_icon=str(ASSETS / "noatech-icon.png"), layout="wide", initial_sidebar_state="auto")
apply_theme()
cfg = load_config()
account_status = authenticate(cfg)
account_header(cfg, account_status)


@st.fragment(run_every=5)
def render():
    try:
        status = portal_client(cfg).request("/status", session=st.session_state.get("account_session"))
        if not status.get("ok"):
            st.session_state.pop("account_session", None)
            st.rerun()
        if not status.get("connected"):
            st.warning("ارتباط با متاتریدر در دسترس نیست. برای اتصال مجدد، از منوی حساب خارج و دوباره وارد شوید.")
    except PortalUnavailable:
        st.error("ارتباط با سرویس اتصال قطع شده است. وضعیت برنامه و متاتریدر را بررسی کنید.")
        return
    try:
        if status.get('view_api') or os.environ.get('NOA_REQUIRE_VIEW_API') == '1':
            response = portal_client(cfg).request('/view', session=st.session_state.get('account_session'))
            if not response.get('ok'):
                if response.get('expired'):
                    st.session_state.pop('account_session', None)
                    st.rerun()
                st.error('داده‌های حساب در دسترس نیست؛ اتصال همین حساب را بررسی کنید.')
                return
            view = response['view']
        else:
            view = read_runtime(cfg.env.database_url, scope=status.get("scope"), since=status.get("started_at"))
    except (OSError, sqlite3.Error, ValueError, PortalUnavailable):
        view = {"heartbeat":None,"analyses":[],"positions":[],"trades":[]}
        st.info("میزکار در حال آماده شدن است؛ دریافت اولین داده‌ها ممکن است کمی زمان ببرد.")
    render_workspace(cfg,status,view)


render()
