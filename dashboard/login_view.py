"""Persian account entry screen for the local desktop application."""
import html

import streamlit as st

from core.account_login import SERVERS
from dashboard.account_client import AccountPortalClient, PortalUnavailable


LOGIN_CSS = """
<style>
.stApp {background:radial-gradient(ellipse at 13% 12%,#102b2d 0,transparent 45%),#090f1a;color:#e9eef6}
header[data-testid="stHeader"] {background:transparent}
[data-testid="stAppDeployButton"],#MainMenu {display:none}
[data-testid="stMainBlockContainer"] {max-width:1180px;padding-top:3rem;padding-bottom:3rem}
h1,h2,h3,p,label,button,input,[data-testid="stMarkdownContainer"],.stAlert {font-family:Tahoma,'Segoe UI',sans-serif!important}
[data-testid="stTextInput"] input {direction:ltr;text-align:left;background:#111c2b;border-radius:10px;min-height:48px}
[data-testid="stSelectbox"] [data-baseweb="select"]>div {background:#111c2b;border-radius:10px;min-height:48px}
[data-testid="stForm"] {background:#101a28;border:1px solid #263549;border-radius:20px;padding:26px}
[data-testid="stFormSubmitButton"] button {width:100%;min-height:49px;background:#32d6ae;border:0;color:#05251d;font-weight:700;border-radius:10px}
[data-testid="stFormSubmitButton"] button:hover {background:#6be7c6;color:#05251d}
[data-testid="stFormSubmitButton"] button:disabled {background:#253846;color:#81939e}
[data-testid="stRadio"]>label,[data-testid="stTextInput"]>label,[data-testid="stCheckbox"] label {direction:rtl}
.brand {display:flex;align-items:center;gap:10px;letter-spacing:.16em;font:700 13px 'Segoe UI',sans-serif;color:#f3f6fb;margin-bottom:30px}
.brand-mark {display:inline-flex;align-items:end;gap:3px;height:23px}
.brand-mark i {display:block;width:5px;border-radius:2px;background:#32d6ae}
.brand-mark i:nth-child(1){height:12px}.brand-mark i:nth-child(2){height:23px}.brand-mark i:nth-child(3){height:17px}
.eyebrow {color:#32d6ae;font:600 11px 'Segoe UI',sans-serif;letter-spacing:.15em;text-transform:uppercase}
.hero {direction:rtl;text-align:right;padding:28px 15px 10px 35px}
.hero h1 {font-size:clamp(32px,3.8vw,49px);line-height:1.65;letter-spacing:-1.6px;margin:12px 0;color:#f1f5fb;font-weight:800}
.hero h1 span {color:#32d6ae}.hero p {color:#94a8ba;font-size:14px;line-height:2.2;max-width:440px}
.hero-chart {margin:24px 0 18px;border:1px solid #203847;border-radius:16px;background:linear-gradient(140deg,#10232c99,#10182799);padding:18px;max-width:440px}
.chart-label {display:flex;justify-content:space-between;direction:ltr;font:11px 'Segoe UI',sans-serif;color:#8eabb8;margin-bottom:10px;letter-spacing:.05em}
.chart-label b {font-weight:600;color:#a5c4ca}.hero-chart svg {width:100%;height:112px}
.hero-pills {display:flex;gap:8px;flex-wrap:wrap;margin-top:20px}
.hero-pills span {border:1px solid #293b4c;border-radius:7px;padding:6px 10px;color:#a7bdcb;font-size:11px}
.login-heading {direction:rtl;text-align:right;margin:12px 0 20px}
.login-heading h2 {font-size:25px;margin:0 0 8px;color:#eef4fa}.login-heading p {font-size:12px;line-height:2;color:#9cabbc}
.login-footer {text-align:center;color:#778c9f;font:11px Tahoma,sans-serif;margin-top:22px;line-height:2}
.account-strip {direction:rtl;text-align:right;padding:14px 20px;background:#10242a;border:1px solid #204a48;border-radius:12px;margin-bottom:14px;color:#c6e5e1;font:13px Tahoma,sans-serif}
.account-strip b {color:#55dfba}
@media(max-width:760px){[data-testid="stMainBlockContainer"]{padding-top:1rem}.brand{margin-bottom:12px}.hero{padding:0 4px}.hero h1{font-size:25px;line-height:1.8;margin:0}.hero p,.hero-chart,.hero-pills,.eyebrow{display:none}.login-heading{margin:0 0 14px}[data-testid="stForm"]{padding:18px}}
</style>
"""

HERO = """
<div class="brand"><span class="brand-mark"><i></i><i></i><i></i></span>QUANT DESK <span style="color:#546b80;font-weight:400">/ MT5</span></div>
<div class="hero">
<div class="eyebrow">YOUR ACCOUNT. ONE CONNECTION.</div>
<h1>حساب شما.<br><span>شروعی ساده‌تر.</span></h1>
<p>به حساب معاملاتی خود متصل شوید و تحلیل بازار، وضعیت ربات و معاملات را از یک‌جا دنبال کنید.</p>
<div class="hero-chart" aria-hidden="true"><div class="chart-label"><b>MARKET OVERVIEW</b><span>MT5 · DIRECT CONNECTION</span></div>
<svg viewBox="0 0 400 112" fill="none"><path d="M0 28H400M0 56H400M0 84H400" stroke="#253b46" stroke-dasharray="3 5"/>
<path d="M2 91L24 86L42 92L62 71L83 76L105 65L125 77L147 55L166 61L188 39L210 48L232 34L253 42L275 22L296 32L318 18L340 27L362 12L395 17" stroke="#34d7ac" stroke-width="2.5"/>
<path d="M2 91L24 86L42 92L62 71L83 76L105 65L125 77L147 55L166 61L188 39L210 48L232 34L253 42L275 22L296 32L318 18L340 27L362 12L395 17V112H2Z" fill="url(#fade)"/>
<defs><linearGradient id="fade" x1="200" y1="0" x2="200" y2="112" gradientUnits="userSpaceOnUse"><stop stop-color="#32d6ae" stop-opacity=".18"/><stop offset="1" stop-color="#32d6ae" stop-opacity="0"/></linearGradient></defs></svg></div>
<div class="hero-pills"><span>اتصال مستقیم به MT5</span><span>حساب دمو و واقعی</span><span>کنترل ریسک فعال</span></div>
</div>
"""


def portal_client(cfg):
    return AccountPortalClient(cfg.env.account_portal_url, cfg.env.account_portal_token_file)


def authenticate(cfg):
    st.markdown(LOGIN_CSS, unsafe_allow_html=True)
    portal = portal_client(cfg)
    session = st.session_state.get("account_session")
    if session:
        try:
            status = portal.request("/status", session=session)
            if status.get("ok"):
                return status
        except PortalUnavailable:
            st.error("ارتباط با رابط ویندوز قطع است. برنامه را با Start.cmd باز کنید.")
            st.stop()
        st.session_state.pop("account_session", None)
    left, right = st.columns([1.05, 1], gap="large")
    with left:
        st.markdown(HERO, unsafe_allow_html=True)
    with right:
        st.markdown('<div class="login-heading"><h2>اتصال حساب معاملاتی</h2><p>مشخصات حساب MT5 خود را وارد کنید. پس از ورود، ربات روی همان حساب فعال می‌شود.</p></div>', unsafe_allow_html=True)
        ready = False
        try:
            ready = bool(portal.request("/status").get("ready"))
        except PortalUnavailable as exc:
            st.error(str(exc))
        st.selectbox("بروکر", ["LiteFinance"], key="login_broker", disabled=True)
        kind = st.radio("نوع حساب", ["demo", "real"], horizontal=True,
            format_func=lambda item: "دمو · آزمایشی" if item == "demo" else "واقعی · ریل", key="login_account_type")
        with st.form("account_login", clear_on_submit=True, border=True):
            login = st.text_input("شماره حساب MT5", placeholder="مثلاً 12345678", max_chars=18, key="login_number")
            password = st.text_input("رمز معاملاتی", type="password", placeholder="رمز حساب متاتریدر", max_chars=256, key="login_password")
            with st.expander("گزینه‌های اتصال"):
                server = st.text_input("سرور حساب", value=SERVERS[kind], key="login_server_" + kind)
                path = st.text_input("مسیر MT5 (اختیاری)", placeholder="تشخیص خودکار ترمینال نصب‌شده", key="login_terminal")
                st.caption("اگر سرور درج‌شده در مشخصات حساب شما متفاوت است، نام دقیق آن را اینجا وارد کنید.")
            confirmed = False
            if kind == "real":
                st.warning("این حساب با پول واقعی معامله می‌کند. ربات پس از اتصال می‌تواند خودکار سفارش ارسال کند.")
                confirmed = st.checkbox("اجازهٔ شروع معاملات خودکار روی حساب واقعی را می‌دهم.", key="login_confirm_real")
            submitted = st.form_submit_button("اتصال و شروع ربات  ←", type="primary", disabled=not ready, use_container_width=True)
        st.caption("رمز شما ذخیره نمی‌شود؛ برای هر نشست دوباره وارد شوید.")
        if submitted:
            try:
                with st.spinner("در حال ورود به متاتریدر و راه‌اندازی ربات…"):
                    result = portal.request("/login", payload={"broker": "LiteFinance", "login": login,
                        "password": password, "account_type": kind, "server": server,
                        "terminal_path": path, "confirm_real": confirmed})
                if result.get("ok"):
                    st.session_state["account_session"] = result["session"]
                    st.rerun()
                st.error(result.get("error", "اتصال انجام نشد. دوباره تلاش کنید."))
            except PortalUnavailable as exc:
                st.error(str(exc))
    st.markdown('<div class="login-footer">QUANT DESK · LiteFinance / MetaTrader 5<br>ورود با شماره و رمز حساب معاملاتی؛ اطلاعات پروفایل وب بروکر قابل استفاده نیست.</div>', unsafe_allow_html=True)
    st.stop()


def account_header(cfg, status):
    account = status.get("account") or {}
    kind = "دمو" if account.get("account_type") == "demo" else "واقعی"
    label = f"LiteFinance · {kind} · {account.get('login', '')} · {account.get('server', '')}"
    st.markdown(f'<div class="account-strip"><b>حساب متصل</b> &nbsp; {html.escape(label)}</div>', unsafe_allow_html=True)
    with st.sidebar:
        st.markdown("### مدیریت حساب")
        st.write("LiteFinance · " + kind)
        st.write(str(account.get("login", "")))
        st.caption(str(account.get("server", "")))
        if st.button("توقف ربات و خروج", use_container_width=True):
            try:
                with st.spinner("در حال توقف ربات…"):
                    result = portal_client(cfg).request("/logout", payload={}, session=st.session_state["account_session"])
                if result.get("ok") or result.get("expired"):
                    st.session_state.pop("account_session", None)
                    st.rerun()
                st.error(result.get("error", "توقف کامل نشد؛ دوباره تلاش کنید."))
            except PortalUnavailable as exc:
                st.error(str(exc))
        st.caption("خروج، موتور ربات را متوقف می‌کند؛ معاملات باز را نمی‌بندد. برای تغییر حساب، دوباره وارد شوید.")
