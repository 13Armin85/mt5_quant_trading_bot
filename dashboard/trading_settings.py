"""Account settings UI. Only authenticated preference saves reach the portal."""
import streamlit as st

from core.trading_preferences import DEFAULTS
from dashboard.account_client import PortalUnavailable
from dashboard.login_view import portal_client
from dashboard.theme import section
from dashboard.workspace import money, stamp


def settings_panel(cfg, status, view):
    account = status.get("account") or {}
    currency = account.get("currency", "ارز حساب")
    preferences = {**DEFAULTS, **(view.get("trading_preferences") or {})}
    identity = str(account.get("login", "account")) + ":" + str(account.get("server", ""))
    section("تنظیمات ترید", "هدف روزانه، سقف ضرر و میزان فعالیت مخصوص همین حساب.", "TRADING SETTINGS")
    daily = (view.get("heartbeat") or {}).get("daily_guard") or {}
    cols = st.columns(3)
    cols[0].metric("نتیجهٔ امروز · " + currency, money(daily.get("pnl")))
    cols[1].metric("هدف سود · " + currency, money(preferences["profit_target"]) if preferences["daily_enabled"] else "غیرفعال")
    cols[2].metric("سقف ضرر · " + currency, money(preferences["loss_limit"]) if preferences["daily_enabled"] else "غیرفعال")
    if daily.get("halt"):
        st.info("هدف سود امروز لمس شده؛ ورود جدید تا روز بعد متوقف است." if daily["halt"] == "DAILY_PROFIT_TARGET"
            else "سقف ضرر امروز لمس شده؛ ورود جدید تا روز بعد متوقف است.")
    st.caption("روز معاملاتی از ساعت ۰۰:۰۰ تهران عوض می‌شود. مبنا، ارزش حساب در اولین پایش هر روز است؛ اگر ربات وسط روز روشن شود، محاسبه از همان زمان آغاز می‌شود. نتیجه شامل سود و ضرر باز و بسته، هزینه‌ها و تغییرات واریز یا برداشت همین حساب است.")
    if daily.get("started_at"):
        st.caption("آغاز محاسبهٔ امروز: " + stamp(daily["started_at"], full=True))
    with st.form("daily_settings:" + identity):
        activity = st.radio("میزان فعالیت", ["normal", "active"],
            index=1 if preferences["activity"] == "active" else 0,
            format_func=lambda x: "فعال‌تر · بررسی مستقل M1 و M5" if x == "active" else "عادی · بررسی ورود در M5",
            key="activity:" + identity)
        st.caption("فعال‌تر: مسیر M1 در هر چرخه و مسیر M5 هر ۳۰ ثانیه یا با کندل جدید بررسی می‌شود؛ بودجهٔ ریسک مشترک و سقف ۲۴ ورود روزانه است. روی هر نماد فقط یک موقعیت باز می‌شود. حد ضرر، کنترل هزینه و فیلتر اخبار حفظ می‌شوند؛ تعداد یا سود بیشتر تضمین نمی‌شود.")
        enabled = st.checkbox("هدف سود و سقف ضرر روزانه فعال باشد", value=preferences["daily_enabled"], key="daily_enabled:" + identity)
        left, right = st.columns(2)
        target = left.number_input("هدف سود روزانه · " + currency, min_value=.01, max_value=100000.,
            value=float(preferences["profit_target"]), step=1., format="%.2f", key="daily_target:" + identity)
        loss = right.number_input("حداکثر ضرر روزانه · " + currency, min_value=.01, max_value=100000.,
            value=float(preferences["loss_limit"]), step=.5, format="%.2f", key="daily_loss:" + identity)
        st.caption("با رسیدن به هر حد، ورود جدید متوقف می‌شود و ربات برای بستن پوزیشن‌های خودش اقدام می‌کند. قبل از ورود نیز لات با بودجهٔ ضرر باقی‌مانده محدود می‌شود. لغزش، گپ، قطع ارتباط یا هزینهٔ خروج می‌تواند نتیجهٔ نهایی را از حد تعیین‌شده عبور دهد.")
        submitted = st.form_submit_button("ذخیره تنظیمات ترید", type="primary", width="stretch")
    if submitted:
        try:
            result = portal_client(cfg).request("/preferences", session=st.session_state.get("account_session"),
                payload={"activity":activity, "daily_enabled":enabled, "profit_target":target, "loss_limit":loss})
            if result.get("ok"):
                st.success("تنظیمات ذخیره شد؛ موتور در چرخهٔ بعدی آن را اعمال می‌کند. خروج از پنل تنظیمات را پاک نمی‌کند.")
            else:
                st.error(result.get("error", "ذخیرهٔ تنظیمات انجام نشد."))
        except PortalUnavailable:
            st.error("ارتباط با سرویس در دسترس نیست؛ ذخیرهٔ تنظیمات تأیید نشد.")
    st.caption("این هدف دستور تضمین سود نیست. تا قبل از رسیدن به هدف، فقط فرصت‌های واجد شرایط بررسی می‌شوند؛ سقف زیان، تعداد معاملات و محدودیت‌های حساب بر هدف سود اولویت دارند. ذخیرهٔ تنظیمات، توقف دستی ترید را لغو نمی‌کند و مبنای محاسبهٔ امروز را تغییر نمی‌دهد.")
