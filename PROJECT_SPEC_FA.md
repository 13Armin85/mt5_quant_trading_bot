تو یک Senior Quant Developer، Algorithmic Trader، Python Engineer و متخصص MetaTrader 5 هستی.

وظیفه تو طراحی و پیاده‌سازی یک ربات ترید کاملاً حرفه‌ای و Production-Grade برای MetaTrader 5 با Python است.

هدف پروژه ساخت سیستمی است که:

- داده‌های بازار را به‌صورت Live از MetaTrader 5 دریافت کند.
- چندین تایم‌فریم را همزمان تحلیل کند.
- Trend، Momentum، Volatility، Volume، Market Structure و Price Action را بررسی کند.
- اخبار اقتصادی و رویدادهای High Impact را در تصمیم‌گیری لحاظ کند.
- فقط در شرایطی معامله کند که Signal Confidence حداقل 70% باشد.
- این عدد 70% نباید به‌عنوان تضمین Win Rate یا سود تعبیر شود؛ باید یک Confidence Score کالیبره‌شده بر اساس مدل، داده تاریخی و Backtest باشد.
- ریسک هر معامله حداکثر 1% Equity حساب باشد.
- Stop Loss و Take Profit کاملاً اجباری باشند.
- قبل از استفاده روی حساب واقعی، سیستم دارای Backtest، Walk-Forward Test و Demo Mode باشد.
- هیچ Martingale، Grid خطرناک یا افزایش حجم برای جبران ضرر استفاده نشود.

سیستم باید Modular، قابل تست، قابل توسعه و قابل تنظیم باشد.

\==================================================

1. TECH STACK
   \==================================================

از این تکنولوژی‌ها استفاده کن:

Python 3.12+

MetaTrader5
pandas
numpy
scipy
scikit-learn
xgboost یا lightgbm در صورت نیاز
pandas-ta یا ta
requests/httpx
pydantic
python-dotenv
SQLAlchemy
SQLite یا PostgreSQL
loguru
pytest

برای Dashboard در صورت نیاز:

FastAPI
WebSocket
React

یا برای نسخه ساده:

Streamlit

تمام نسخه‌های Dependencyها را در requirements.txt یا pyproject.toml مشخص کن.

# ================================================== 2. PROJECT STRUCTURE

پروژه را حداقل با ساختاری مشابه زیر بساز:

trading\_bot/
│
├── main.py
├── config.py
├── .env.example
├── requirements.txt
├── README.md
│
├── config/
│ ├── settings.yaml
│ ├── symbols.yaml
│ └── strategy.yaml
│
├── core/
│ ├── mt5\_client.py
│ ├── data\_feed.py
│ ├── execution.py
│ ├── risk\_manager.py
│ ├── position\_manager.py
│ └── trading\_engine.py
│
├── analysis/
│ ├── indicators.py
│ ├── trend.py
│ ├── momentum.py
│ ├── volatility.py
│ ├── volume.py
│ ├── market\_structure.py
│ ├── support\_resistance.py
│ ├── price\_action.py
│ └── regime\_detector.py
│
├── signals/
│ ├── signal\_engine.py
│ ├── confidence\_engine.py
│ └── filters.py
│
├── news/
│ ├── news\_provider.py
│ ├── economic\_calendar.py
│ ├── sentiment.py
│ └── news\_filter.py
│
├── ml/
│ ├── features.py
│ ├── model.py
│ ├── training.py
│ ├── calibration.py
│ └── model\_registry.py
│
├── backtest/
│ ├── engine.py
│ ├── walk\_forward.py
│ ├── metrics.py
│ └── report.py
│
├── database/
│ ├── models.py
│ └── repository.py
│
├── monitoring/
│ ├── logger.py
│ ├── alerts.py
│ └── health\_check.py
│
└── tests/
├── test\_risk.py
├── test\_signals.py
├── test\_execution.py
└── test\_backtest.py

هر فایل باید مسئولیت مشخص داشته باشد.

از God Class و فایل‌های چند هزار خطی خودداری کن.

# ================================================== 3. CONNECTION TO METATRADER 5

یک MT5Client حرفه‌ای پیاده‌سازی کن.

قابلیت‌ها:

initialize()
shutdown()
reconnect()
health\_check()

دریافت:

account\_info
terminal\_info
symbol\_info
symbol\_info\_tick
positions\_get
orders\_get

دریافت داده:

copy\_rates\_from
copy\_rates\_from\_pos
copy\_rates\_range
copy\_ticks\_from
copy\_ticks\_range

در صورت پشتیبانی Broker:

market\_book\_add
market\_book\_get
market\_book\_release

برای ارسال سفارش:

order\_check
order\_calc\_margin
order\_calc\_profit
order\_send

تمام Error Codeها مدیریت شوند.

اگر اتصال MT5 قطع شد:

معامله جدید باز نشود.

سیستم تلاش کند Reconnect شود.

تا زمانی که اتصال سالم نشده Trading متوقف بماند.

Credentialها هرگز Hard-code نشوند.

از:

.env

استفاده کن.

مثلاً:

MT5\_LOGIN=
MT5\_PASSWORD=
MT5\_SERVER=
MT5\_PATH=

# ================================================== 4. LIVE MARKET DATA

سیستم باید اطلاعات بازار را Real-Time دریافت کند.

برای هر Symbol:

Bid
Ask
Spread
OHLC
Tick Volume
Real Volume اگر Broker ارائه می‌دهد
Ticks
Market Depth اگر موجود باشد

را دریافت کند.

حداقل تایم‌فریم‌های زیر قابل تنظیم باشند:

M1
M5
M15
M30
H1
H4
D1

اما Strategy نباید مجبور باشد همه آنها را استفاده کند.

Multi-Timeframe Analysis بساز.

مثلاً:

H4 → Macro Trend
H1 → Main Trend
M15 → Setup
M5 → Entry

تمام Timeframeها از Configuration قابل تغییر باشند.

# ================================================== 5. INDICATORS

Indicator Engine بساز.

حداقل موارد زیر را پشتیبانی کند:

EMA 20
EMA 50
EMA 100
EMA 200

SMA

RSI 14

MACD
MACD Signal
MACD Histogram

ADX

ATR

Bollinger Bands

Stochastic RSI

VWAP در بازارهایی که داده مناسب دارند.

OBV

Volume Moving Average

از Indicatorها کورکورانه استفاده نکن.

هدف ترکیب چند دسته Signal مستقل است.

# ================================================== 6. MARKET STRUCTURE

ماژولی برای تشخیص Market Structure ایجاد کن.

موارد زیر را تشخیص بده:

Higher High
Higher Low
Lower High
Lower Low

Uptrend
Downtrend
Range

Swing High
Swing Low

Breakout
False Breakout

Support
Resistance

Dynamic Support/Resistance

Previous Day High
Previous Day Low

Previous Week High
Previous Week Low

در صورت امکان:

Break of Structure
Change of Character

اما تعریف آنها دقیق و Algorithmic باشد، نه Subjective.

# ================================================== 7. PRICE ACTION

Price Action Engine داشته باش.

تشخیص:

Pin Bar
Engulfing
Inside Bar
Outside Bar
Doji

Bullish / Bearish rejection

Breakout + Retest

Trend Pullback

Range Rejection

Momentum Candle

تمام Patternها باید با Trend و Context ترکیب شوند.

صرف دیدن یک Candlestick Pattern نباید Signal ایجاد کند.

# ================================================== 8. VOLUME ANALYSIS

Volume Analysis بسیار مهم است.

سیستم بررسی کند:

Current Tick Volume

Volume SMA

Relative Volume

Volume Spike

Volume Expansion

Volume Contraction

Breakout Volume Confirmation

اگر Real Volume موجود بود:

Real Volume را در اولویت قرار بده.

اگر Real Volume موجود نبود:

Tick Volume استفاده شود و در Log مشخص باشد.

در صورت موجود بودن Market Depth:

Order Book Imbalance محاسبه کن.

مثلاً:

Bid Volume / Ask Volume

ولی فقط در صورتی استفاده شود که Broker داده معتبر Market Depth ارائه دهد.

نباید داده جعلی ساخته شود.

# ================================================== 9. MARKET REGIME DETECTION

قبل از انتخاب Strategy، وضعیت Market Regime مشخص شود.

حالت‌های احتمالی:

TRENDING\_BULLISH
TRENDING\_BEARISH
RANGING
HIGH\_VOLATILITY
LOW\_VOLATILITY
BREAKOUT
NEWS\_EVENT
UNCERTAIN

Regime Detection از:

ADX
ATR
EMA structure
Volatility
Market Structure
Volume

استفاده کند.

Strategy براساس Regime انتخاب شود.

مثلاً:

Trend Following در Trending Market.

Mean Reversion در Range.

Breakout Strategy در Compression → Expansion.

اگر Regime نامطمئن بود:

NO TRADE.

# ================================================== 10. NEWS ENGINE

یک NewsEngine حرفه‌ای ایجاد کن.

Architecture آن Provider-Based باشد.

یعنی بتوان Provider اخبار را عوض کرد.

مثلاً:

Economic Calendar Provider
Financial News Provider
Sentiment Provider

از APIهای معتبر در صورت داشتن API Key استفاده کن.

Credential:

NEWS\_API\_KEY=
ECONOMIC\_API\_KEY=

در صورت نیاز امکان Bridge جداگانه با MQL5 Economic Calendar را طراحی کن.

هیچ API غیرواقعی اختراع نکن.

اگر API Key وجود نداشت:

News Module باید Gracefully Disable شود.

# ================================================== 11. ECONOMIC CALENDAR

رویدادهای مهم اقتصادی را تشخیص بده:

Interest Rate Decision

CPI

Core CPI

NFP

Unemployment

GDP

PPI

Retail Sales

FOMC

ECB

BoE

BoJ

Fed speeches

Central Bank decisions

رویدادها براساس:

LOW
MEDIUM
HIGH

دسته‌بندی شوند.

قبل و بعد از High Impact News معامله جدید باز نشود.

Configuration:

NEWS\_BLOCK\_BEFORE\_MINUTES=30
NEWS\_BLOCK\_AFTER\_MINUTES=30

قابل تغییر باشد.

برای هر Symbol تشخیص بده خبر مربوط به کدام Currency است.

مثلاً:

EURUSD

خبر EUR یا USD باید بررسی شود.

XAUUSD

اخبار USD، نرخ بهره آمریکا، Fed، CPI، NFP و موارد مؤثر قابل تنظیم باشند.

# ================================================== 12. NEWS SENTIMENT

در صورت وجود Provider مناسب، News Sentiment ایجاد کن.

مثلاً:

VERY\_BEARISH
BEARISH
NEUTRAL
BULLISH
VERY\_BULLISH

اما Sentiment نباید به‌تنهایی معامله باز کند.

فقط به‌عنوان یکی از Featureها وارد Signal Engine شود.

اگر News Data قدیمی، قطع یا نامعتبر بود:

سیستم News Confidence را صفر کند.

# ================================================== 13. SIGNAL ENGINE

SignalEngine مرکز تصمیم‌گیری باشد.

برای هر Symbol خروجی مشابه زیر داشته باشد:

signal = BUY / SELL / HOLD

confidence = 0.00 - 1.00

entry\_price

stop\_loss

take\_profit

risk\_reward

reasons

market\_regime

trend\_score

momentum\_score

volume\_score

structure\_score

price\_action\_score

news\_score

volatility\_score

spread\_score

تمام دلایل Signal باید قابل مشاهده باشند.

مثلاً:

BUY
Confidence: 0.78

Reasons:

H4 bullish trend
H1 EMA50 > EMA200
M15 bullish structure
ADX = 31
RSI = 57
MACD bullish
volume = 1.6x average
breakout confirmed
news risk = low

این Explainability الزامی است.

# ================================================== 14. 70% CONFIDENCE

قانون بسیار مهم:

ربات فقط زمانی اجازه Entry داشته باشد که:

confidence >= 0.70

اما 0.70 نباید عدد تصادفی باشد.

دو حالت پیاده‌سازی کن.

MODE A:

Rule-Based Confidence.

مثلاً وزن‌ها:

Trend 20%
Market Structure 20%
Momentum 15%
Volume 15%
Price Action 10%
Volatility 10%
News 10%

وزن‌ها Configurable باشند.

MODE B:

Machine Learning Probability.

یک Classification Model بساز که احتمال مثبت بودن Trade را تخمین بزند.

Featureها:

RSI
MACD
ADX
ATR
EMA distances
trend
market structure
volume
spread
session
volatility
news proximity
momentum
returns
candle structure

Target مثلاً:

آیا Price قبل از برخورد به SL به TP خواهد رسید؟

مدل باید:

Train
Validation
Test

داشته باشد.

Data Leakage کاملاً ممنوع.

Time-Series Split استفاده شود.

# ================================================== 15. PROBABILITY CALIBRATION

بسیار مهم:

اگر Model خروجی Probability می‌دهد باید Calibration انجام شود.

از روش‌هایی مانند:

Platt Scaling
Isotonic Regression

در صورت مناسب بودن استفاده کن.

Calibration Curve ایجاد کن.

مثلاً:

Signalهایی که Model برای آنها Confidence حدود 0.70 داده واقعاً بررسی شوند که در Out-of-Sample Data چه Win Probability داشته‌اند.

اگر مدل Calibration مناسبی ندارد:

اجازه استفاده Live داده نشود.

# ================================================== 16. ENTRY CONDITIONS

برای BUY حداقل شرایط:

Trend تأیید شود.

Market Structure bullish باشد.

Momentum مخالف شدید نباشد.

Spread مناسب باشد.

Volatility مناسب باشد.

Volume تایید داشته باشد.

High Impact News نزدیک نباشد.

Risk/Reward قابل قبول باشد.

Confidence >= 0.70

برای SELL معکوس همین منطق.

در صورت Conflict شدید بین Timeframeها:

NO TRADE.

# ================================================== 17. RISK MANAGEMENT

مهم‌ترین بخش پروژه RiskManager است.

حداکثر ریسک هر Trade:

1% Account Equity

یعنی:

risk\_amount = equity \* 0.01

Lot Size باید براساس:

Entry Price
Stop Loss
Tick Size
Tick Value
Contract specification
Volume Step
Minimum Volume
Maximum Volume

به صورت Dynamic محاسبه شود.

هیچ Lot Size ثابت استفاده نکن.

مثال:

Equity = $10,000

Risk = 1%

Maximum Loss:

$100

اگر SL بخورد:

ضرر تقریبی نباید از $100 بیشتر شود؛ با درنظرگرفتن محدودیت‌های حجم Broker.

از مشخصات واقعی Symbol در MT5 استفاده کن.

# ================================================== 18. STOP LOSS

تمام معاملات باید SL داشته باشند.

SL می‌تواند براساس:

ATR

Swing High/Low

Market Structure

Support/Resistance

باشد.

از Stop Loss بسیار نزدیک یا غیرمنطقی جلوگیری کن.

Minimum Stop Distance Broker بررسی شود.

# ================================================== 19. TAKE PROFIT

Take Profit براساس:

Risk/Reward

Structure

Support/Resistance

یا ATR

باشد.

حداقل Risk Reward پیش‌فرض:

1:1.5

ترجیح:

1:2+

ولی در Configuration قابل تغییر باشد.

اگر Setup از نظر آماری Edge ندارد فقط برای رسیدن به RR مصنوعی، TP دور قرار نده.

# ================================================== 20. PORTFOLIO RISK

فقط Risk هر Trade کافی نیست.

قوانین Portfolio Risk:

MAX\_RISK\_PER\_TRADE = 1%

MAX\_TOTAL\_OPEN\_RISK = 3%

MAX\_DAILY\_LOSS = 3%

MAX\_WEEKLY\_LOSS = 6%

MAX\_CONSECUTIVE\_LOSSES = 3

بعد از رسیدن به Daily Loss Limit:

Trading متوقف شود.

تا Session بعدی معامله باز نشود.

# ================================================== 21. CORRELATION PROTECTION

از باز کردن چند Position بسیار Correlated جلوگیری کن.

مثلاً:

EURUSD BUY
GBPUSD BUY

ممکن است هر دو USD exposure مشابه ایجاد کنند.

Correlation Matrix براساس Historical Returns ایجاد کن.

اگر Exposure بیش از حد شد:

Trade جدید Reject شود.

# ================================================== 22. SPREAD FILTER

قبل از Entry:

Spread بررسی شود.

اگر Spread غیرعادی بود:

NO TRADE.

Current Spread را با Median/Average Historical Spread مقایسه کن.

مثلاً:

spread > average\_spread \* configurable\_multiplier

→ Reject.

# ================================================== 23. SLIPPAGE

Slippage مدیریت شود.

Maximum Deviation قابل تنظیم باشد.

اگر Fill غیرعادی بود:

Log ایجاد شود.

اگر Market شرایط غیرعادی داشت:

Trade لغو شود.

# ================================================== 24. ORDER EXECUTION

Execution Engine باید قبل از ارسال معامله:

Connection Check

Market Open Check

Symbol Availability

Spread Check

Risk Check

News Check

Existing Position Check

Correlation Check

Margin Check

Order Check

را اجرا کند.

سپس:

order\_send()

بعد نتیجه را Validate کند.

هیچ وقت فرض نکند order\_send موفق بوده.

retcode بررسی شود.

# ================================================== 25. DUPLICATE TRADE PROTECTION

سیستم نباید روی یک Signal چند بار معامله باز کند.

Signal ID تولید کن.

مثلاً:

symbol
timeframe
direction
candle\_time
strategy

را Hash کن.

اگر Signal قبلاً اجرا شده:

دوباره Entry نده.

# ================================================== 26. POSITION MANAGEMENT

PositionManager ایجاد کن.

قابلیت‌ها:

Break Even

Trailing Stop

Partial Close

Time-Based Exit

Signal-Based Exit

اما همه Optional و Configurable باشند.

پیش‌فرض رفتار محافظه‌کارانه باشد.

Trailing Stop فقط وقتی فعال شود که Trade مقدار مناسبی وارد Profit شده باشد.

# ================================================== 27. TRADING SESSIONS

Session Detection:

Sydney
Tokyo
London
New York

را پیاده‌سازی کن.

Strategy بتواند براساس Session Filter داشته باشد.

Sessionها براساس timezone صحیح محاسبه شوند.

DST را نادیده نگیر.

# ================================================== 28. MACHINE LEARNING

Machine Learning باید Optional باشد.

ابتدا Baseline Rule-Based Strategy بساز.

بعد مدل ML.

Modelهای قابل بررسی:

Logistic Regression

Random Forest

XGBoost

LightGBM

هدف انتخاب پیچیده‌ترین مدل نیست.

مدلی انتخاب شود که در Out-of-Sample Data عملکرد پایدارتر دارد.

Feature Importance نیز نمایش داده شود.

# ================================================== 29. NO DATA LEAKAGE

هیچ Future Data در Training Featureها استفاده نشود.

Normalization فقط روی Training Data Fit شود.

TimeSeriesSplit استفاده شود.

Random Shuffle برای Time Series ممنوع مگر دلیل فنی مشخص داشته باشد.

# ================================================== 30. OVERFITTING PROTECTION

برای جلوگیری از Overfitting:

Train / Validation / Test

Walk Forward Analysis

Parameter Stability

Cross-Market Test

Cross-Timeframe Test

استفاده کن.

Strategy نباید فقط روی یک Symbol و دوره خاص خوب باشد.

# ================================================== 31. BACKTEST ENGINE

Backtester حرفه‌ای بساز.

باید موارد زیر را در نظر بگیرد:

Spread

Commission

Slippage

Swap در صورت نیاز

Bid/Ask differences

Position sizing

Stop Loss

Take Profit

Trading sessions

News filter

در صورت نبود دیتای واقعی برای چیزی، آن را شفاف اعلام کن؛ دیتای ساختگی را به‌عنوان واقعیت استفاده نکن.

# ================================================== 32. BACKTEST METRICS

Metrics:

Total Trades

Winning Trades

Losing Trades

Win Rate

Profit Factor

Expectancy

Net Profit

Average Win

Average Loss

Average R

Max Drawdown

Relative Drawdown

Sharpe Ratio

Sortino Ratio

Calmar Ratio

Recovery Factor

Maximum Consecutive Wins

Maximum Consecutive Losses

Average Holding Time

Exposure

Monthly Return

# ================================================== 33. WALK-FORWARD TEST

Walk Forward Analysis پیاده‌سازی کن.

مثلاً:

Training:
12 Months

Validation:
3 Months

Test:
3 Months

سپس Window جلو برود.

نتایج تمام Windowها گزارش شود.

فقط نتیجه بهترین Window نشان داده نشود.

# ================================================== 34. MONTE CARLO

Monte Carlo Simulation برای Trade Results ایجاد کن.

مثلاً:

1000 simulations

بررسی:

Drawdown Distribution

Expected Return Distribution

Probability of severe drawdown

Risk of ruin

را گزارش کن.

# ================================================== 35. PAPER TRADING

سه Mode داشته باش:

BACKTEST

PAPER

LIVE

Default:

PAPER

برای فعال شدن LIVE نیاز به:

ENABLE\_LIVE\_TRADING=true

باشد.

اگر False:

حتی اگر Signal ایجاد شد معامله واقعی ارسال نشود.

# ================================================== 36. LIVE SAFETY

قبل از LIVE Trading:

Connection healthy

Data fresh

Spread valid

Risk valid

Model valid

News data valid

Account mode verified

Symbol tradable

باشد.

Kill Switch ایجاد کن.

مثلاً:

kill\_switch.flag

اگر فایل وجود داشت:

هیچ معامله جدیدی باز نشود.

# ================================================== 37. STALE DATA PROTECTION

اگر آخرین Tick قدیمی‌تر از Threshold بود:

Trading متوقف شود.

مثلاً:

MAX\_TICK\_AGE\_SECONDS

اگر Data Feed Fail شد:

NO TRADE.

# ================================================== 38. LOGGING

تمام اتفاقات Log شوند.

نمونه:

2026-09-12 14:30

XAUUSD

SIGNAL BUY

Confidence: 0.76

Entry: ...

SL: ...

TP: ...

Risk: 1%

Volume: ...

RR: ...

Trend: bullish

Volume confirmation: yes

News risk: low

همچنین Rejectها Log شوند.

مثلاً:

REJECTED:
Confidence 0.62 < 0.70

یا:

REJECTED:
High impact USD news in 12 minutes.

# ================================================== 39. TRADE DATABASE

تمام معاملات در Database ذخیره شوند.

Fields:

id

signal\_id

timestamp

symbol

direction

entry

sl

tp

lot

risk

confidence

market\_regime

spread

volume\_score

trend\_score

news\_score

reason

ticket

exit\_price

profit

profit\_r

duration

status

# ================================================== 40. DASHBOARD

یک Dashboard ایجاد کن.

نمایش:

Account Balance

Equity

Free Margin

Daily P/L

Weekly P/L

Drawdown

Current Positions

Open Risk

Current Signals

Signal Confidence

Market Regime

News Events

Bot Status

MT5 Connection

Trading Mode

آخرین معاملات

# ================================================== 41. SIGNAL DASHBOARD

جدولی مانند:

Symbol | Signal | Confidence | Trend | Volume | News | RR

XAUUSD | BUY | 78% | Bullish | Strong | Safe | 1:2.3

EURUSD | HOLD | 61% | Neutral | Weak | Safe | -

GBPUSD | SELL | 73% | Bearish | Strong | Safe | 1:1.9

نمایش بده.

# ================================================== 42. ALERT SYSTEM

Alertها قابل توسعه باشند.

مثلاً:

Telegram

Email

Discord

Console

در Signal Alert بنویس:

Symbol
BUY / SELL
Confidence
Entry
SL
TP
Lot
Risk
RR
Reason

Credentialها فقط از Environment Variable گرفته شوند.

# ================================================== 43. CONFIGURATION

settings.yaml نمونه:

trading:

mode: PAPER

risk\_per\_trade: 0.01

max\_total\_risk: 0.03

max\_daily\_loss: 0.03

max\_weekly\_loss: 0.06

min\_confidence: 0.70

min\_rr: 1.5

max\_consecutive\_losses: 3

news:

enabled: true

block\_before\_minutes: 30

block\_after\_minutes: 30

strategy:

timeframes:

```
macro: H4

trend: H1

setup: M15

entry: M5

```

تمام پارامترها قابل تغییر باشند.

# ================================================== 44. DEFAULT SYMBOLS

سیستم Multi-Symbol باشد.

نمونه:

EURUSD
GBPUSD
USDJPY
XAUUSD

ولی Symbol Names ممکن است بین Brokerها فرق کنند.

مثلاً:

XAUUSD
XAUUSDm
GOLD

بنابراین Symbol Mapping ایجاد کن.

# ================================================== 45. TESTING

Unit Testهای جدی بساز.

مخصوصاً:

Risk Calculation

Lot Calculation

SL calculation

Confidence calculation

Duplicate Order Protection

News Filter

Spread Filter

Daily Loss Lock

Maximum Open Risk

Order Validation

# ================================================== 46. RISK TEST

تست کن:

Equity = 10000 USD

Risk = 1%

Maximum theoretical loss ≈ 100 USD

برای چند Symbol مختلف:

EURUSD

GBPUSD

XAUUSD

USDJPY

تست بنویس.

به خاطر تفاوت Tick Value و Contract Size نباید یک فرمول ساده ثابت برای همه Symbolها استفاده شود.

# ================================================== 47. ERROR HANDLING

سیستم در هیچ خطایی Crash بی‌دلیل نکند.

اما همچنین خطا را مخفی نکند.

اگر Component حیاتی Fail شد:

Trading متوقف شود.

Fail-Safe > Fail-Open

مثلاً اگر News Provider قطع شد و Strategy به News وابسته است:

معامله نکن.

# ================================================== 48. PERFORMANCE

Indicatorهای کامل تاریخی را هر Tick از ابتدا محاسبه نکن.

Cache ایجاد کن.

Incremental updates استفاده کن.

API rate limits رعایت شوند.

CPU Usage کنترل شود.

# ================================================== 49. SECURITY

Password

Login

API Keys

Telegram Token

Database credentials

نباید داخل Git ذخیره شوند.

.env

در:

.gitignore

باشد.

.env.example

بدون Secret واقعی ایجاد کن.

# ================================================== 50. README

README بسیار کامل باشد.

شامل:

Prerequisites

Python installation

MetaTrader 5 installation

Virtual environment

Dependency installation

.env setup

Broker login

Run Backtest

Run Paper Trading

Run Live Trading

Dashboard

Training ML Model

Testing

Troubleshooting

# ================================================== 51. WINDOWS SETUP

چون MetaTrader 5 معمولاً روی Windows اجرا می‌شود، راه‌اندازی برای Windows را کامل توضیح بده.

نمونه:

python -m venv .venv

.venv\Scripts\activate

pip install -r requirements.txt

python main.py

اما دستورها را براساس ساختار واقعی پروژه تنظیم کن.

# ================================================== 52. IMPLEMENTATION RULES

هیچ Placeholder غیرضروری باقی نگذار.

ننویس:

# TODO implement later

تا حد امکان Featureها را واقعاً پیاده‌سازی کن.

اگر یک Feature نیازمند API خارجی است:

Interface کامل آن را بنویس.

Provider Sample ایجاد کن.

Environment Variables را تعریف کن.

Failure Handling را پیاده‌سازی کن.

# ================================================== 53. CODE QUALITY

رعایت:

SOLID

DRY

Separation of Concerns

Type Hints

Dataclasses یا Pydantic Models

Docstrings

Exception Handling

Logging

Dependency Injection در جاهای مناسب

# ================================================== 54. TRADING PRINCIPLE

اصل اصلی سیستم:

NO TRADE یک تصمیم معتبر است.

ربات مجبور نیست همیشه در بازار Position داشته باشد.

اگر Setup ضعیف است:

HOLD.

اگر Confidence:

0.69

باشد:

حتی اگر Signal خوب به نظر برسد:

NO TRADE.

# ================================================== 55. NO GUARANTEES

در طراحی فرض نکن که:

70% confidence = 70% قطعی Win Rate.

Confidence باید Calibration و Out-of-Sample Validation داشته باشد.

هیچ Strategy سود تضمینی ندارد.

هدف سیستم:

Positive Expected Value

Controlled Risk

Low Drawdown

Consistency

Robustness

است.

# ================================================== 56. OPTIMIZATION OBJECTIVE

Strategy را صرفاً برای بیشترین Net Profit Optimize نکن.

Objective ترکیبی باشد.

مثلاً:

Profit Factor

Expectancy

Max Drawdown

Sharpe

Stability

Trade Count

Out-of-Sample Performance

هدف:

Robust Strategy

نه Backtest زیبا و Overfit شده.

# ================================================== 57. FINAL VALIDATION

قبل از اجازه LIVE:

حداقل شروط قابل تنظیم:

Minimum trades >= 200

Profit Factor >= 1.3

Positive Expectancy

Acceptable Maximum Drawdown

Positive Out-of-Sample performance

Walk-forward stability

Probability calibration acceptable

No major data leakage

No execution errors

این Thresholdها Configurable باشند و به‌عنوان تضمین سود معرفی نشوند.

# ================================================== 58. IMPLEMENTATION PHASES

پروژه را مرحله‌ای توسعه بده:

PHASE 1

MT5 connection

Live data

Indicators

Basic signals

Risk manager

Order execution

PHASE 2

Market structure

Volume

Multi timeframe

Regime detection

News

PHASE 3

Backtester

Metrics

Walk Forward

Monte Carlo

PHASE 4

Machine Learning

Probability Calibration

Feature engineering

PHASE 5

Dashboard

Alerts

Database

Monitoring

PHASE 6

Paper Trading validation

PHASE 7

Optional Live Mode

هر Phase باید قبل از Phase بعدی قابل اجرا و تست باشد.

# ================================================== 59. FINAL OUTPUT REQUIRED

من فقط توضیح تئوری نمی‌خواهم.

کل پروژه را پیاده‌سازی کن.

خروجی باید شامل:

1. Tree کامل پروژه
2. تمام Source Codeها
3. requirements.txt یا pyproject.toml
4. .env.example
5. Configuration files
6. Database models
7. MT5 connector
8. Live Data Engine
9. Indicator Engine
10. Volume Analysis
11. Market Structure
12. Signal Engine
13. Confidence Engine
14. News Engine
15. Risk Manager
16. Order Execution
17. Position Manager
18. Backtest Engine
19. Walk Forward
20. Monte Carlo
21. ML module
22. Tests
23. Dashboard
24. README
25. Windows installation instructions
26. Running instructions
27. Demo/Paper Trading instructions
28. Live Mode safety instructions

# ================================================== 60. BEFORE WRITING CODE

قبل از پیاده‌سازی:

ابتدا Architecture پروژه را طراحی کن.

سپس Data Flow را مشخص کن:

MT5
↓
Market Data
↓
Feature Engine
↓
Market Regime
↓
News Filter
↓
Signal Engine
↓
Confidence Engine
↓
Risk Manager
↓
Execution Engine
↓
Position Manager
↓
Database / Monitoring

سپس Implementation را شروع کن.

در حین پیاده‌سازی، سؤال‌های غیرضروری از من نپرس.

اگر جزئیاتی مشخص نشده، یک Default منطقی و محافظه‌کارانه انتخاب کن و آن را Configurable قرار بده.

هیچ ادعای تضمین سود نکن.

هدف ساخت یک Quant Trading Framework حرفه‌ای، قابل تست و قابل توسعه برای MetaTrader 5 است که در حالت پیش‌فرض روی PAPER TRADING اجرا شود و تنها پس از Backtest و Validation مناسب امکان فعال‌سازی LIVE داشته باشد.