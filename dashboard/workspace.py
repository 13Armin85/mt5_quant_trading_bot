"""Persian presentation of stored runtime data. No MT5 or execution operations."""
from collections import Counter
from datetime import datetime, timezone
import math
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st

from dashboard.theme import badge, e, empty_state, fa, icon, section, strategy_empty_state


TEHRAN = ZoneInfo("Asia/Tehran")
STATES = {
    "FRESH": "قیمت تازه", "MARKET_CLOSED": "بازار بسته", "CLOCK_MISMATCH": "اختلاف ساعت",
    "STALE": "قیمت قدیمی", "NO_TICK": "بدون قیمت", "LIVE": "به‌روز", "UNAVAILABLE": "در انتظار داده",
    "OUT_OF_SYNC": "در انتظار هماهنگی", "AWAITING_NEW_BAR": "در انتظار کندل جدید",
    "INVALID_BAR": "دادهٔ نامعتبر", "BUY": "خرید", "SELL": "فروش", "HOLD": "انتظار",
    "OPEN": "باز", "CLOSED": "بسته‌شده", "PARTIAL": "اجرای جزئی", "PAPER_OPEN": "باز · شبیه‌ساز",
    "PAPER_CLOSED": "بسته · شبیه‌ساز", "REJECTED": "ردشده", "UNKNOWN": "نیازمند بررسی",
    "PROTECTION_FAILURE": "نیازمند بررسی حفاظت", "SIGNAL": "سیگنال", "ACTIVE": "فعال",
    "PAUSED": "متوقف", "LOW": "کم", "MEDIUM": "متوسط", "HIGH": "زیاد",
    "TRENDING": "رونددار", "RANGING": "رنج", "VOLATILE": "پرنوسان", "UNCERTAIN": "نامشخص",
    "BASE": "عادی", "CAUTIOUS": "محافظه‌کار", "STRONG_SETUP": "ستاپ قوی", "baseline": "نمای پایه",
    "scalping": "اسکالپ", "intraday": "روزانه",
    "TRENDING_BULLISH": "روند صعودی", "TRENDING_BEARISH": "روند نزولی", "HIGH_VOLATILITY": "نوسان زیاد",
    "LOW_VOLATILITY": "نوسان کم", "BREAKOUT": "شکست محدوده", "NEWS_EVENT": "رویداد خبری",
}
STRATEGIES = {
    "trend_pullback": "پولبک در روند", "breakout": "شکست محدوده", "mean_reversion": "بازگشت به میانگین",
    "momentum": "مومنتوم چندبازه‌ای", "ema_cross": "تقاطع میانگین‌ها", "macd_cross": "تقاطع MACD",
    "ichimoku": "ابر ایچیموکو", "supertrend": "سوپرترند", "stochastic": "بازگشت استوکاستیک",
    "cci": "بازگشت CCI", "keltner": "شکست کلتنر", "vwap": "بازگشت VWAP", "ensemble": "ترکیب استراتژی‌ها",
    "stochastic_reversal": "بازگشت استوکاستیک", "cci_reversion": "بازگشت CCI",
    "keltner_breakout": "شکست کلتنر", "vwap_reclaim": "بازگشت VWAP", "baseline": "روش پایه",
}
DECISIONS = {
    "MARKET_CLOSED": ("در انتظار بازگشایی بازار", "پس از بازگشایی بازار و دریافت قیمت تازه، بررسی شرایط ورود ادامه پیدا می‌کند."),
    "CLOCK_MISMATCH": ("ساعت قیمت نیاز به بررسی دارد", "زمان سیستم و زمان قیمت متاتریدر هم‌خوان نیست؛ تا رفع اختلاف، ورود جدید انجام نمی‌شود."),
    "DATA_STALE": ("در انتظار قیمت تازه", "برای ارزیابی قابل اتکا، ربات به قیمت و دادهٔ تازه نیاز دارد."),
    "WARMUP": ("در حال آماده‌سازی تحلیل", "تاریخچهٔ کندل‌ها و داده‌های چندبازه‌ای در حال آماده‌سازی است."),
    "NEWS": ("در انتظار تأیید وضعیت اخبار", "شرایط اخبار و تقویم اقتصادی باید پیش از ورود به معامله تأیید شوند."),
    "SPREAD": ("هزینهٔ ورود مناسب نیست", "اسپرد فعلی از محدودهٔ مجاز عبور کرده است؛ ربات بازار را همچنان پایش می‌کند."),
    "COST": ("هزینه‌های معامله نیاز به بررسی دارند", "هزینهٔ ورود نسبت به فاصلهٔ حد ضرر برای این ستاپ مناسب نیست."),
    "NO_SETUP": ("در انتظار ستاپ معاملاتی", "بازار در حال پایش است؛ ورود زمانی انجام می‌شود که شرایط استراتژی تأیید شود."),
    "SCORE": ("تأیید کافی برای ورود وجود ندارد", "امتیاز قواعد و تأیید استراتژی‌های مستقل هنوز به حد لازم نرسیده است."),
    "DIRECTION_CONFLICT": ("استراتژی‌ها هم‌جهت نیستند", "برای جلوگیری از تصمیم متناقض، ربات تا روشن‌تر شدن شرایط منتظر می‌ماند."),
    "RR": ("نسبت بازده به ریسک کافی نیست", "هدف قیمتی پس از احتساب هزینه‌ها، شرایط لازم برای ورود را ندارد."),
    "TOTAL_RISK": ("بودجهٔ ریسک در دسترس نیست", "تا آزاد شدن بودجهٔ ریسک، موقعیت جدیدی باز نمی‌شود."),
    "KILL_SWITCH": ("ورودهای جدید متوقف شده‌اند", "توقف ورود فعال است؛ مدیریت موقعیت‌های قبلی مطابق وضعیت موتور ادامه پیدا می‌کند."),
    "EXECUTION_UNKNOWN": ("نتیجهٔ اجرا نیازمند بررسی است", "ربات برای جلوگیری از ارسال تکراری سفارش، منتظر روشن شدن وضعیت اجرا می‌ماند."),
    "LOSS_PAUSE": ("توقف طبق محدودیت زیان", "برای ادامه، وضعیت ریسک و زمان مجاز شروع مجدد باید بررسی شود."),
    "LOSS_LIMIT": ("محدودیت زیان فعال شده است", "ورود جدید طبق کنترل‌های ریسک متوقف شده است."),
}


def translated(value, mapping=STATES):
    return mapping.get(str(value), str(value) if value is not None else "—")


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def stamp(value, *, full=False):
    if not value:
        return "—"
    try:
        instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        return fa(instant.astimezone(TEHRAN).strftime("%Y/%m/%d · %H:%M" if full else "%H:%M:%S"))
    except (ValueError, TypeError, OverflowError):
        return "—"


def age_seconds(heartbeat, now):
    try:
        return (now - datetime.fromisoformat(heartbeat["time"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None


def money(value, currency=""):
    value = number(value)
    return (fa(f"{value:,.2f}") + (" " + currency if currency else "")) if value is not None else "—"


def render_metrics(heartbeat, positions, account, age):
    fresh = age is not None and 0 <= age < 180
    equity, exposure = number(heartbeat.get("equity")), number(heartbeat.get("planned_open_risk"))
    ratio = exposure / equity if equity and equity > 0 and exposure is not None else None
    values = [
        ("ارزش حساب" + (f" · {account['currency']}" if account.get("currency") else ""), money(equity)),
        ("پوزیشن‌های ربات", fa(len(positions)) if heartbeat else "—"),
        ("ریسک باز حساب", fa(f"{ratio * 100:.2f}") + "٪" if ratio is not None else "—"),
        ("آخرین به‌روزرسانی", fa(max(0, int(age))) + " ثانیه" if age is not None and age >= 0 else "—"),
    ]
    with st.container(key="noatech_metrics"):
        for column, (label, value) in zip(st.columns(4), values):
            column.metric(label, value)
    if not fresh:
        st.warning("داده‌های نمایش‌داده‌شده تازه نیستند. اتصال و وضعیت اجرای ربات را بررسی کنید؛ اطلاعات قبلی مبنای ورود جدید قرار نمی‌گیرد.")


def quote_rows(quotes, filter_name):
    result = []
    for quote in quotes.values():
        status = quote.get("status", "UNAVAILABLE")
        if filter_name == "قیمت تازه" and status != "FRESH":
            continue
        if filter_name == "بازار بسته" and status != "MARKET_CLOSED":
            continue
        if filter_name == "نیازمند بررسی" and status in {"FRESH", "MARKET_CLOSED"}:
            continue
        result.append({"نماد": quote.get("symbol", "—"), "وضعیت بازار": translated(status),
            "فروش · Bid": number(quote.get("bid")), "خرید · Ask": number(quote.get("ask")),
            "آخرین قیمت · تهران": stamp(quote.get("last_tick_utc"), full=True)})
    return result


def overview(cfg, heartbeat, view, account):
    quotes = heartbeat.get("quotes") or {}
    counts = Counter(q.get("status", "UNAVAILABLE") for q in quotes.values())
    details, main = st.columns([1, 1.8], gap="large")
    with main, st.container(key="noa_market_card"):
        section("دیده‌بان بازار", "قیمت‌ها و وضعیت دسترسی نمادهای متصل به حساب شما.", "MARKET WATCH")
        st.markdown('<div class="nv-inline-status">' + badge(f'{fa(counts["FRESH"])} قیمت تازه', "good") + badge(f'{fa(counts["MARKET_CLOSED"])} بازار بسته') + badge(f'{fa(len(quotes))} نماد') + '</div>', unsafe_allow_html=True)
        selected = st.selectbox("نمایش نمادها", ["همه نمادها", "قیمت تازه", "بازار بسته", "نیازمند بررسی"], key="noatech_quote_filter", label_visibility="collapsed")
        rows = quote_rows(quotes, selected)
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True, height=min(425, 38 + len(rows) * 35),
                column_config={"فروش · Bid": st.column_config.NumberColumn(), "خرید · Ask": st.column_config.NumberColumn()})
        else:
            empty_state("هنوز نمادی برای نمایش وجود ندارد", "پس از دریافت دادهٔ متاتریدر، وضعیت نمادها اینجا نمایش داده می‌شود.", "globe")
    with details:
        section("در یک نگاه", "وضعیت موتور، اخبار و کنترل ریسک.", "WORKSPACE STATUS")
        risk = heartbeat.get("risk_state") or {}
        active = risk.get("status", "ACTIVE") == "ACTIVE"
        mode = heartbeat.get("mode", "—")
        st.markdown(f'<div class="nv-card"><div class="nv-card-head"><span>{icon("activity")}موتور معاملات</span>{badge("فعال" if heartbeat.get("connected") else "در انتظار", "good" if heartbeat.get("connected") else "warn")}</div><h4>{"پایش بازار در جریان است" if heartbeat.get("connected") else "آماده‌سازی میزکار"}</h4><p>اجرای سفارش از طریق متاتریدر · <bdi>{e(mode)}</bdi><br>آخرین دادهٔ ثبت‌شده: {e(stamp(heartbeat.get("time")))}</p></div>', unsafe_allow_html=True)
        ceiling = float(cfg.get("trading.max_total_risk", .01))
        equity, exposure = number(heartbeat.get("equity")), number(heartbeat.get("planned_open_risk"))
        fraction = exposure / equity if equity and equity > 0 and exposure is not None else None
        progress = min(100, max(0, fraction / ceiling * 100)) if fraction is not None and ceiling > 0 else 0
        current = fa(f"{fraction * 100:.2f}") + "٪" if fraction is not None else "نامشخص"
        st.markdown(f'<div class="nv-card"><div class="nv-card-head"><span>{icon("shield")}بودجهٔ ریسک</span>{badge("فعال" if active else "توقف طبق محدودیت", "good" if active else "warn")}</div><div class="nv-card-number">{e(current)}</div><div class="nv-progress"><span style="width:{progress:.2f}%"></span></div><div class="nv-split"><span>ریسک باز</span><span>سقف {fa(f"{ceiling * 100:g}")}٪</span></div><p style="margin-top:12px">ریسک پایهٔ هر ورود: {fa(f"{float(cfg.get('trading.risk_per_trade', .005)) * 100:g}")}٪</p></div>', unsafe_allow_html=True)
        news = heartbeat.get("news") or {}
        news_ready = bool(news.get("background_running") and news.get("calendar_ok") and not news.get("last_error") and not news.get("worker_error") and not news.get("unavailable_headline_currencies"))
        st.markdown(f'<div class="nv-card"><div class="nv-card-head"><span>{icon("globe")}اخبار و تقویم</span>{badge("به‌روز" if news_ready else "در انتظار بررسی", "good" if news_ready else "warn")}</div><p>وضعیت منابع و رویدادهای پیش رو را در بخش «اخبار بازار» دنبال کنید.</p></div>', unsafe_allow_html=True)


def decision_summary(heartbeat):
    decision = heartbeat.get("last_decision") or {}
    if not isinstance(decision, dict):
        decision = {}
    title, description = DECISIONS.get(decision.get("reason_code"), ("بازار در حال بررسی است", "نواتک شرایط ورود و مدیریت موقعیت‌ها را بر اساس آخرین داده‌های موجود بررسی می‌کند."))
    st.markdown(f'<div class="nv-brief">{icon("spark",22)}<div><b>{e(title)}</b><p>{e(description)}</p></div></div>', unsafe_allow_html=True)


def analysis_panel(cfg, analyses):
    section("تحلیل نمادها", "جزئیات هر نماد، نتیجهٔ استراتژی‌ها و داده‌های فنی را کنار هم ببینید.", "SYMBOL INSIGHTS")
    analyses = [a for a in analyses if isinstance(a, dict) and a.get("symbol")]
    if not analyses:
        empty_state("اولین تحلیل در راه است", "پس از آماده شدن قیمت و کندل‌های کامل، تحلیل نمادها در این بخش قرار می‌گیرد.", "layers")
        return
    choices = {f"{a['symbol']} · {translated(a.get('profile','baseline'))}": a for a in analyses}
    selected = st.selectbox("انتخاب نماد و نمای تحلیل", list(choices), key="noatech_analysis")
    analysis = choices[selected]
    direction = analysis.get("direction", "HOLD")
    tone = "good" if direction == "BUY" else "bad" if direction == "SELL" else "muted"
    strategy = translated(analysis.get("strategy", "ensemble"), STRATEGIES)
    st.markdown(f'<div class="nv-card"><div class="nv-card-head"><span>{icon("activity")}<bdi>{e(analysis["symbol"])}</bdi> · {e(strategy)}</span>{badge(translated(direction),tone)}</div><p>آخرین تحلیل: {e(stamp(analysis.get("updated_at"),full=True))}</p></div>', unsafe_allow_html=True)
    decision = DECISIONS.get(analysis.get("reason_code"))
    if decision:
        st.info(decision[0] + "؛ " + decision[1])
    selection = analysis.get("selection") or {}
    assessments = selection.get("assessments") or []
    content, technical = st.columns([1.45, 1], gap="large")
    with content:
        section("مقایسهٔ استراتژی‌ها", "امتیازها میزان تطابق قواعد را نشان می‌دهند، نه احتمال سود.")
        if assessments:
            data = [{"استراتژی": translated(a.get("strategy"),STRATEGIES), "امتیاز": number(a.get("score")) or 0,
                "جهت": translated(a.get("direction")), "شرایط ورود": "تأییدشده" if a.get("eligible") else "تأیید نشده"} for a in assessments]
            fig = go.Figure(go.Bar(x=[a["امتیاز"] for a in data], y=[a["استراتژی"] for a in data], orientation="h",
                marker_color=["#7964E8" if a.get("eligible") else "#858C99" for a in assessments],
                hovertemplate="%{y}<br>امتیاز: %{x:.2f}<extra></extra>"))
            fig.update_layout(height=max(245, len(data)*26+40), margin=dict(l=10,r=20,t=10,b=20),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Vazirmatn",size=11),
                xaxis=dict(range=[0,1],zeroline=False),yaxis=dict(autorange="reversed",gridcolor="rgba(0,0,0,0)"))
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False}, key="noatech_strategy_chart")
            st.dataframe(data, width="stretch", hide_index=True)
        else:
            strategy_empty_state()
    with technical:
        section("قیمت و برنامهٔ معامله", "اطلاعات ثبت‌شده در آخرین ارزیابی نماد.")
        chart = analysis.get("live_chart") or {}
        c1,c2 = st.columns(2)
        c1.metric("فروش · Bid", f"{number(chart.get('bid')):g}" if number(chart.get("bid")) is not None else "—")
        c2.metric("خرید · Ask", f"{number(chart.get('ask')):g}" if number(chart.get("ask")) is not None else "—")
        plan = analysis.get("trade_plan") or {}
        if plan:
            rows = [{"جزئیات برنامه": label, "مقدار": str(plan[key]) if plan.get(key) is not None else "—"}
                for key,label in (("entry","ورود"),("sl","حد ضرر"),("tp","حد سود"),("net_rr","نسبت بازده به ریسک خالص"))]
            st.dataframe(rows,width="stretch",hide_index=True)
        sizing = analysis.get("sizing") or {}
        if sizing:
            st.caption("سطح ریسک: " + translated(sizing.get("tier")) + " · سقف ریسک این ورود: " + fa(f"{float(sizing.get('risk_fraction',0))*100:g}") + "٪")
        st.caption("داده‌های کندل در حال تشکیل، مقدماتی‌اند؛ تصمیم استراتژی‌ها بر مبنای کندل بسته‌شده است.")
    indicators = analysis.get("indicators") or {}
    if indicators:
        with st.expander("اندیکاتورها و نوسان‌نماها"):
            roles = {"macro":"دید کلان", "trend":"روند", "setup":"آماده‌سازی", "entry":"ورود"}
            role = st.selectbox("بازهٔ تحلیل", list(indicators),format_func=lambda v:roles.get(v,v),key="noatech_indicator_role")
            rows=[]
            for group, values in indicators[role].items():
                if isinstance(values,dict):
                    for name,value in values.items():
                        rows.append({"گروه": {"trend":"روند","oscillators":"نوسان‌نما","volatility":"نوسان","volume":"حجم"}.get(group,group),
                            "اندیکاتور":name.upper(),"مقدار":f"{number(value):.6g}" if number(value) is not None else "—"})
            if rows:
                st.dataframe(rows,width="stretch",hide_index=True)
    if analysis.get("candidate_plans"):
        with st.expander("برنامه‌های معاملاتی بررسی‌شده"):
            st.dataframe([{k:v for k,v in plan.items() if k != "trade_plan"} for plan in analysis["candidate_plans"]],width="stretch",hide_index=True)
    with st.expander("گزارش فنی این تحلیل"):
        st.json(analysis)


def news_panel(news):
    section("اخبار بازار", "سلامت منابع، تقویم اقتصادی و تازه‌ترین مطالب منتشرشده.", "NEWS & ECONOMIC CALENDAR")
    left,right = st.columns([1,1.65],gap="large")
    with left:
        section("وضعیت منابع", "آخرین بررسی اتصال؛ مستقل از تاریخ انتشار خبر.")
        sources = [{"name":"تقویم اقتصادی", "ok":news.get("calendar_ok"),"checked_at":news.get("calendar_checked_at")}] + (news.get("headlines") or [])
        names={"Federal Reserve Monetary Policy":"سیاست پولی فدرال رزرو","Federal Reserve Speeches":"سخنرانی‌های فدرال رزرو","European Central Bank":"بانک مرکزی اروپا"}
        html=''
        for source in sources:
            html += f'<div class="nv-source"><div>{e(names.get(source.get("name"),source.get("name","منبع اخبار")))}<small>آخرین بررسی: {e(stamp(source.get("checked_at")))}</small></div>{badge("در دسترس" if source.get("ok") else "در انتظار", "good" if source.get("ok") else "warn")}</div>'
        st.markdown('<div class="nv-card">'+html+'</div>',unsafe_allow_html=True)
        if news.get("last_error") or news.get("worker_error"):
            st.warning("دریافت بخشی از اخبار کامل نشده است. کنترل اخبار پیش از هر ورود همچنان بررسی می‌شود.")
        if news.get("refresh_in_progress"):
            st.caption("در حال دریافت آخرین اطلاعات اخبار…")
    with right:
        section("رویدادهای پیش رو", "زمان‌ها به وقت تهران نمایش داده می‌شوند.")
        upcoming = news.get("upcoming_events") or []
        if upcoming:
            st.dataframe([{"زمان · تهران":stamp(row.get("time"),full=True),"ارز":row.get("currency","—"),
                "اهمیت":translated(row.get("impact")),"رویداد":row.get("title","")} for row in upcoming],width="stretch",hide_index=True)
        else:
            empty_state("رویدادی برای نمایش وجود ندارد", "پس از دریافت تقویم اقتصادی، رویدادهای پیش رو اینجا قرار می‌گیرند.","clock")
    section("آخرین خبرها", "عنوان اصلی مطالب حفظ شده است. زمان انتشار به وقت تهران است.")
    headlines = news.get("latest_headlines") or []
    if not headlines:
        empty_state("خبر تازه‌ای دریافت نشده است", "با به‌روزرسانی منابع، آخرین مطالب در این بخش نمایش داده می‌شوند.","globe")
    for row in headlines[:8]:
        url = str(row.get("url", ""))
        title = e(row.get("title", ""))
        if urlparse(url).scheme in {"http","https"}:
            title = f'<a href="{e(url)}" target="_blank" rel="noopener noreferrer" class="nv-news-link">{title}</a>'
        st.markdown(f'<div class="nv-card"><div class="nv-card-head"><span>{e(row.get("source",""))}</span><span>{e(stamp(row.get("time"),full=True))}</span></div><h4 style="font-size:13px;line-height:1.9;direction:auto">{title}</h4><p>{e(row.get("currency",""))} · اهمیت {e(translated(row.get("impact")))}</p></div>',unsafe_allow_html=True)


def render_profit_loss(view, currency=""):
    pnl = view.get("pnl") or {}
    suffix = f" · {currency}" if currency else ""
    for column, label, key in zip(st.columns(3),
            ("سود و ضرر کل ربات", "سود و ضرر معاملات بسته", "سود و ضرر پوزیشن‌های باز"),
            ("total_net", "closed_net", "open_net")):
        value = number(pnl.get(key))
        # Isolate the sign with its number inside the Persian RTL layout.
        display = "\u2066" + fa(f"{value:+,.2f}") + "\u2069" if value is not None else "—"
        column.metric(label + suffix, display)
    st.caption("جمع کل از تمام تاریخچهٔ معاملات همین حساب و حالت معاملاتی محاسبه می‌شود. نتیجهٔ معاملات بسته خالصِ کارمزد و سواپ است؛ نتیجهٔ پوزیشن‌های باز تا زمان بسته‌شدن تغییر می‌کند و هزینهٔ احتمالی خروج هنوز قطعی نیست.")
    if pnl.get("unsettled_count"):
        st.caption("تسویهٔ بعضی معاملات هنوز کامل نیست؛ مجموع نهایی پس از دریافت نتیجه نمایش داده می‌شود.")


def trade_profit(row, positions):
    if row.get("status") in {"CLOSED", "PAPER_CLOSED"}:
        return number(row.get("profit"))
    if row.get("ticket") is None:
        return None
    return next((number(p.get("profit")) for p in positions if p.get("ticket") == row["ticket"]), None)


def trades_panel(view, currency=""):
    section("معاملات و موقعیت‌ها", "موقعیت‌های ثبت‌شده و آخرین اجراهای همین حساب.", "POSITIONS & EXECUTIONS")
    positions = view.get("positions") or []
    profit_label = "سود و ضرر خالص" + (f" · {currency}" if currency else "")
    profit_config = {profit_label: st.column_config.NumberColumn(format="%+.2f")}
    section("پوزیشن‌های باز ربات")
    if positions:
        st.dataframe([{"نماد":row.get("symbol"),"جهت":translated(row.get("direction")),"وضعیت":translated(row.get("status")),
            "شناسه":str(row.get("ticket","—")),profit_label:number(row.get("profit")),
            "نمای معاملاتی":translated(row.get("profile","baseline"))} for row in positions],width="stretch",hide_index=True,column_config=profit_config)
        st.caption("نتیجهٔ باز شامل سود و ضرر جاری، هزینه‌های ثبت‌شده و خروج‌های جزئی است. خط تیره یعنی داده یا تسویه هنوز در دسترس نیست.")
    else:
        empty_state("پوزیشن بازی برای ربات ثبت نشده", "با بازشدن موقعیت، نماد، جهت معامله و شناسهٔ آن را همین‌جا می‌بینید.", "layers",
            fields=("نماد", "جهت", "سود و ضرر", "شناسه"))
    section("تاریخچهٔ اجراها", "آخرین ۱۰۰ رکورد ذخیره‌شدهٔ حساب انتخابی.")
    trades = view.get("trades") or []
    if not trades:
        empty_state("تاریخچه از اولین اجرا شروع می‌شود", "سفارش‌ها و نتیجهٔ اجراهای این حساب، پس از ثبت در این بخش نمایش داده می‌شوند.", "clock",
            fields=("زمان", "نماد", "حجم", "سود و ضرر"))
        return
    statuses = sorted({str(row.get("status", "UNKNOWN")) for row in trades})
    choice = st.selectbox("فیلتر وضعیت",["ALL",*statuses],format_func=lambda key:"همهٔ وضعیت‌ها" if key=="ALL" else translated(key),key="noatech_trade_filter")
    records=[row for row in trades if choice=="ALL" or row.get("status")==choice]
    st.dataframe([{"زمان · تهران":stamp(row.get("timestamp"),full=True),"نماد":row.get("symbol"),
        "جهت":translated(row.get("direction")),"حجم · لات":row.get("lot"),"وضعیت":translated(row.get("status")),
        profit_label:trade_profit(row, positions), "شناسه":str(row.get("ticket") or "—"),
        "امتیاز قواعد":row.get("confidence"),"وضعیت بازار":translated(row.get("market_regime"))} for row in records],width="stretch",hide_index=True,column_config=profit_config)
    st.caption("عدد مثبت سود و عدد منفی ضرر است. نتیجهٔ ردیف‌های باز موقت است؛ نتیجهٔ نهایی پس از تسویه ثبت می‌شود.")
    with st.expander("جزئیات فنی اجراها"):
        st.dataframe(records,width="stretch",hide_index=True)


def render_workspace(cfg, status, view):
    now = datetime.now(timezone.utc)
    heartbeat = view.get("heartbeat") or {}
    account = status.get("account") or {}
    age = age_seconds(heartbeat,now)
    if status.get('trading_requested') is False:
        st.info('ترید این حساب با درخواست شما متوقف است. خروج و ورود دوباره آن را فعال نمی‌کند؛ برای ادامه از «شروع ترید» استفاده کنید. داده‌های قبلی ممکن است قدیمی باشند.')
    st.markdown(f'<div class="nv-page-heading"><div><span class="nv-eyebrow">NOATECH / WORKSPACE</span><h1>نمای کلی میزکار</h1><p>تصویری روشن از حساب، بازار و تصمیم‌های ربات.</p></div><div class="nv-clock">{fa(now.astimezone(TEHRAN).strftime("%H:%M"))}<span>به وقت تهران · به‌روزرسانی خودکار</span></div></div>',unsafe_allow_html=True)
    if heartbeat.get("phase")=="starting":
        st.info("حساب متصل است؛ نواتک داده‌های بازار را برای اولین تحلیل آماده می‌کند.")
    decision_summary(heartbeat)
    render_metrics(heartbeat,view.get("positions") or [],account,age)
    render_profit_loss(view, account.get("currency", ""))
    overview_tab,analysis_tab,news_tab,trades_tab = st.tabs(["نمای کلی","تحلیل نمادها","اخبار بازار","معاملات"])
    with overview_tab:
        overview(cfg,heartbeat,view,account)
    with analysis_tab:
        analysis_panel(cfg,view.get("analyses") or [])
    with news_tab:
        news_panel(heartbeat.get("news") or {})
    with trades_tab:
        trades_panel(view, account.get("currency", ""))
    with st.expander("جزئیات فنی وضعیت سیستم"):
        st.json(heartbeat)
    st.markdown('<div class="nv-footer"><span>نواتک · تحلیل بازار، پایش معاملات، کنترل ریسک.</span><span>NOATECH / YOUR TRADING WORKSPACE</span></div>',unsafe_allow_html=True)
