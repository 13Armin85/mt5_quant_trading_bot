"""NoaTech account entry and account controls, with credentials kept in memory."""
import streamlit as st

from core.account_login import SERVERS
from dashboard.account_client import AccountPortalClient, PortalUnavailable
from dashboard.theme import badge, brand, e, fa, icon


WORKSPACE_ART = """
<div class="nv-workspace-art" aria-hidden="true">
<div class="nv-art-head"><span>نمایی از فضای تحلیل نواتک</span><div class="nv-art-dots"><i></i><i></i><i></i></div></div>
<svg class="nv-art-graph" viewBox="0 0 450 132" fill="none">
<defs><linearGradient id="nv-area" x1="220" y1="10" x2="220" y2="132" gradientUnits="userSpaceOnUse"><stop stop-color="#816BFF" stop-opacity=".14"/><stop offset="1" stop-color="#816BFF" stop-opacity="0"/></linearGradient></defs>
<path d="M0 24H450M0 57H450M0 90H450M0 123H450" stroke="#242735" stroke-dasharray="2 6"/>
<path d="M8 96C28 96 28 78 46 80S65 103 83 91 99 58 120 65 146 83 164 65 177 79 197 59 226 66 247 47 267 57 286 44 309 50 328 26 350 41 374 25 399 33 419 17 439 23" stroke="#816BFF" stroke-width="2.5"/>
<path d="M8 96C28 96 28 78 46 80S65 103 83 91 99 58 120 65 146 83 164 65 177 79 197 59 226 66 247 47 267 57 286 44 309 50 328 26 350 41 374 25 399 33 419 17 439 23V132H8Z" fill="url(#nv-area)"/>
<path d="M15 113 32 109 52 110 73 103 95 109 117 99 137 102 157 90 179 95 200 84 221 90 243 82 265 86 285 73 305 76 328 68 352 72 374 65 397 70 420 61 440 62" stroke="#808597" stroke-width="1.2" opacity=".55"/>
<circle cx="439" cy="23" r="4" fill="#BCAFFF" stroke="#161824" stroke-width="3"/>
</svg>
<div class="nv-art-foot"><span>تحلیل چندبازه‌ای</span><span>پایش اخبار</span><span>مدیریت ریسک</span></div>
</div>
"""


def portal_client(cfg):
    return AccountPortalClient(cfg.env.account_portal_url, cfg.env.account_portal_token_file)


def authenticate(cfg):
    portal = portal_client(cfg)
    session = st.session_state.get("account_session")
    if session:
        try:
            status = portal.request("/status", session=session)
            if status.get("ok"):
                return status
        except PortalUnavailable:
            st.error("ارتباط با متاتریدر در دسترس نیست. نواتک را با Start.cmd دوباره باز کنید.")
            st.stop()
        st.session_state.pop("account_session", None)

    ready = False
    real_trading = True
    connection_error = None
    try:
        readiness = portal.request('/status')
        ready = bool(readiness.get('ready'))
        real_trading = readiness.get('real_trading', True)
    except PortalUnavailable:
        connection_error = "سرویس اتصال آماده نیست. برای شروع، فایل Start.cmd را باز کنید."
    st.markdown('<span class="nv-login-page"></span>', unsafe_allow_html=True)
    state = badge("آمادهٔ اتصال" if ready else "در انتظار راه‌اندازی", "good" if ready else "warn")
    st.markdown(f'<div class="nv-topbar">{brand()}<div class="nv-topbar-meta">{state}<span>متصل به دنیای MetaTrader 5</span></div></div>', unsafe_allow_html=True)
    introduction, form_column = st.columns([1.08, 1], gap="large")
    with introduction:
        st.markdown(f'<div class="nv-login-intro"><span class="nv-eyebrow">NOA</span><h1>معاملات شما،<br>روشن و منظم.</h1><p>حساب، تحلیل بازار و وضعیت ربات را<br>در یک فضای ساده دنبال کنید.</p><div class="nv-login-features"><span>{icon("wallet",18)}نمای یکپارچهٔ حساب</span><span>{icon("activity",18)}پایش خودکار بازار</span><span>{icon("shield",18)}کنترل روشنِ ریسک</span></div></div>', unsafe_allow_html=True)
    with form_column:
        with st.container(key="noatech_login_card"):
            st.markdown(f'<div class="nv-login-heading"><span>{icon("link",16)} یک اتصال، یک میزکار</span><h2>به نواتک خوش آمدید</h2><p>برای ورود، اطلاعات حساب معاملاتی MT5 خود را وارد کنید.</p></div>', unsafe_allow_html=True)
            if connection_error:
                st.info(connection_error)
            st.selectbox("بروکر", ["LiteFinance"], key="login_broker", disabled=True)
            kind = st.radio("نوع حساب", ["demo", "real"], horizontal=True,
                format_func=lambda item: "حساب دمو" if item == "demo" else "حساب واقعی", key="login_account_type", width="stretch")
            with st.form("account_login", clear_on_submit=True, border=False):
                login = st.text_input("شماره حساب MT5", placeholder="12345678", max_chars=18, key="login_number")
                password = st.text_input("رمز معاملاتی", type="password", placeholder="رمز ورود به حساب متاتریدر", max_chars=256, key="login_password")
                with st.expander("تنظیمات اتصال"):
                    server = st.text_input("سرور حساب", value=SERVERS[kind], key="login_server_" + kind)
                    path = st.text_input("مسیر MT5 (اختیاری)", placeholder="ترمینال نصب‌شده خودکار پیدا می‌شود", key="login_terminal")
                    st.caption("سرور پیش‌فرض با نوع حساب تنظیم می‌شود. اگر سرور حساب شما متفاوت است، نام دقیق آن را وارد کنید.")
                confirmed = False
                if kind == "real":
                    if real_trading:
                        st.warning("پس از ورود، ربات می‌تواند با موجودی واقعی این حساب معامله کند.")
                        confirmed = st.checkbox("اجازهٔ شروع معاملات خودکار روی حساب واقعی را می‌دهم.", key="login_confirm_real")
                    else:
                        st.info('حساب واقعی در این سرور فقط برای پایش و شبیه‌سازی متصل می‌شود؛ سفارش واقعی ارسال نمی‌شود.')
                        confirmed = st.checkbox('اتصال حساب واقعی برای پایش و شبیه‌سازی را تأیید می‌کنم.', key='login_confirm_real')
                submitted = st.form_submit_button("اتصال و ورود به نواتک", type="primary", disabled=not ready, use_container_width=True)
            st.markdown(f'<div class="nv-secret-note">{icon("shield",15)} رمز شما برای اتصال امن به حساب استفاده می‌شود.</div>', unsafe_allow_html=True)
            st.caption('از رمز معاملاتی MT5 استفاده کنید. خروج از پنل، ترید را متوقف نمی‌کند؛ حسابی که توقف کرده‌اید فقط با دکمهٔ شروع دوباره فعال می‌شود.')
            if submitted:
                try:
                    with st.spinner("در حال اتصال حساب و آماده‌سازی میزکار نواتک…"):
                        result = portal.request("/login", payload={"broker": "LiteFinance", "login": login,
                            "password": password, "account_type": kind, "server": server,
                            "terminal_path": path, "confirm_real": confirmed})
                    if result.get("ok"):
                        st.session_state["account_session"] = result["session"]
                        st.rerun()
                    st.error(result.get("error", "ورود انجام نشد. اطلاعات حساب را بررسی و دوباره تلاش کنید."))
                except PortalUnavailable:
                    st.error("ارتباط با سرویس اتصال قطع شد. نواتک را با Start.cmd دوباره باز کنید.")
    st.markdown('<div class="nv-footer"><span>نواتک، میزکار شما برای تحلیل و پایش معاملات.</span><span>NOATECH · DESIGNED FOR CLARITY</span></div>', unsafe_allow_html=True)
    st.stop()


def account_header(cfg, status):
    account = status.get("account") or {}
    kind = "حساب دمو" if account.get("account_type") == "demo" else "حساب واقعی"
    connected = status.get("connected", False)
    connection = badge("متصل به متاتریدر" if connected else "اتصال قطع است", "good" if connected else "bad")
    st.markdown(f'<div class="nv-topbar"><span class="nv-workspace-label">میزکار معاملات</span><div class="nv-topbar-meta" role="status" aria-live="polite">{connection}{badge(kind,"accent")}</div></div>', unsafe_allow_html=True)
    with st.sidebar:
        st.markdown(brand(""), unsafe_allow_html=True)
        st.markdown('<div class="nv-side-caption">حساب معاملاتی شما</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="nv-account-card">{badge(kind,"accent")}<div class="nv-account-id">{e(account.get("login", "—"))}</div><b style="font-size:12px;color:var(--nv-text)">LiteFinance</b><small>{e(account.get("server", ""))}</small></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="nv-card-head"><span>{icon("link",17)} وضعیت حساب</span>{badge("متصل" if connected else "قطع", "good" if connected else "bad")}</div>', unsafe_allow_html=True)
        st.caption("وضعیت تحلیل‌ها و معاملات هر ۵ ثانیه به‌روز می‌شود.")
        st.divider()
        if status.get('trading_requested', True):
            if st.button('توقف ترید', key='noa_stop_trading', use_container_width=True):
                try:
                    result = portal_client(cfg).request('/stop', payload={}, session=st.session_state['account_session'])
                    if result.get('ok'):
                        st.rerun()
                    st.error(result.get('error', 'توقف ترید تأیید نشد؛ وضعیت را بررسی و دوباره تلاش کنید.'))
                except PortalUnavailable:
                    st.error('توقف ترید تأیید نشد؛ ارتباط با سرویس در دسترس نیست.')
        else:
            st.info('ترید این حساب با درخواست شما متوقف است.')
            if st.button('شروع ترید', key='noa_start_trading', type='primary', use_container_width=True):
                try:
                    result = portal_client(cfg).request('/start', payload={}, session=st.session_state['account_session'])
                    if result.get('ok'):
                        st.rerun()
                    st.error(result.get('error', 'شروع ترید تأیید نشد.'))
                except PortalUnavailable:
                    st.error('شروع ترید تأیید نشد؛ ارتباط با سرویس در دسترس نیست.')
        st.caption('توقف ترید، موتور این حساب را متوقف می‌کند؛ پوزیشن‌های باز بسته نمی‌شوند و مدیریت خودکارشان نیز متوقف می‌شود.')
        if st.button("خروج از پنل", use_container_width=True):
            try:
                with st.spinner("در حال پایان دادن به نشست نواتک…"):
                    result = portal_client(cfg).request("/logout", payload={}, session=st.session_state["account_session"])
                if result.get("ok") or result.get("expired"):
                    st.session_state.pop("account_session", None)
                    st.rerun()
                st.error(result.get("error", "خروج کامل نشد؛ دوباره تلاش کنید."))
            except PortalUnavailable:
                st.error("ارتباط با سرویس اتصال در دسترس نیست. وضعیت برنامه را بررسی کنید.")
        st.caption('خروج از پنل و بستن مرورگر، اتصال حساب و ترید را متوقف نمی‌کند.')
        with st.expander("راهنمای کوتاه نواتک"):
            st.write("دمو، حساب آزمایشی بروکر است. برچسب LIVE یعنی سفارش‌ها از طریق متاتریدر اجرا می‌شوند؛ نوع حساب را در کارت بالا ببینید.")
            st.write("برای تعویض حساب، ابتدا خارج شوید و سپس اطلاعات حساب بعدی را وارد کنید.")
            st.write('برای توقف ترید باید وارد همین حساب شوید و دکمهٔ «توقف ترید» را بزنید. خروج یا پایان نشست مرورگر، موتور را متوقف نمی‌کند.')
        st.markdown('<div class="nv-side-caption">NOATECH / MT5 WORKSPACE</div>', unsafe_allow_html=True)
