# MT5 Quant Trading Bot

See [the updated Persian execution/research guide](IMPROVEMENTS_FA.md) and [the engineering audit with real broker data](RESEARCH_AUDIT_FA.md). Candidates are ranked across symbols before entry; directional portfolio factors are independent of news mappings. The chronological backtester reports mark-to-market equity and resolution limits. Research experiments remain on `baseline`.

A modular Python 3.12+ trading framework for MetaTrader 5. The project is deliberately **PAPER-first**. A confidence score is not a guarantee of win rate or profit, and `LIVE` execution is blocked unless it is explicitly enabled.

The configured LiteFinance watchlist contains 21 broker-verified symbols: nine FX pairs, gold and silver, WTI and Brent oil, natural gas, four indices, and BTC, ETH and SOL CFDs. See `config/symbols.yaml` for exact MT5 names, quote hours, cost limits and portfolio risk groups. Crypto uses an estimated 0.12% notional fee in account currency; confirm account-specific charges and swap before live use. More symbols increase the set of possible signals, not the chance of profit. The engine still limits risk per entry and total open risk. A running Docker trader must be rebuilt and restarted to load the code changes; its dashboard alone does not apply them.

**Docker Desktop LIVE startup:** [راهنمای فارسی Docker](DOCKER_FA.md). On Windows, `Start-Live.cmd` starts the authenticated native MT5 bridge and the Linux `trader` service with `--mode live`. The bridge keeps the terminal password on the host; persistent SQLite history, single-writer leases and request deduplication survive container restarts. `Stop-Live.cmd` stops both components without closing broker positions. Docker Desktop and MT5 must be available; `ENABLE_LIVE_TRADING=true`, trading permissions, fresh quotes, news checks and a qualified signal are still required.

During `run`, a background news worker publishes complete snapshots while chart analysis and position management continue on the MT5 thread. Pending, failed or stale snapshots block new entries; news is checked again immediately before submission. A read-only dashboard at `http://localhost:8501` refreshes chart assessments, indicators, news health/headlines and executions every five seconds. `Start-Live.cmd` starts both Compose services; `docker compose up -d --no-build dashboard` starts the monitor alone.

**راهنمای اتصال حساب و اجرای ویندوز:** [راهنمای فارسی](SETUP_FA.md).

**استراتژی‌ها، اندیکاتورها و انتخاب نهایی:** [راهنمای مقایسه ۱۲ روش](STRATEGIES_FA.md).

The default `strategy.family: ensemble` evaluates 12 configured closed-bar rules and records every assessment: trend pullback, range breakout, Bollinger re-entry, multi-timeframe momentum, EMA/MACD crosses, Ichimoku cloud break, Supertrend flip, Stochastic/CCI reversals, Keltner break and rolling VWAP reclaim. Related rules share one confirmation group. Qualified opposing directions abstain. Scores describe rule suitability, never win probabilities. `auto` uses the same selector; an explicit family restricts selection to that rule.

Indicators now include Stochastic K/D, CCI, Williams %R, ROC, causal Ichimoku/Supertrend, Keltner and prior-bar Donchian channels alongside the original EMA/SMA, RSI/StochRSI, MACD, ADX/DMI, ATR/Bollinger, OBV and rolling VWAP. Zero/missing volume does not become fabricated real volume.

```powershell
python main.py analyze --symbol EURUSD --output reports/eurusd_analysis.json
```

This read-only command reports the latest stored closed bars even outside market hours. It never initializes an execution engine or sends orders. Its final direction is HOLD because contemporaneous news/execution are not validated; `selection` contains the technical proposal, all rule assessments and scores. Runtime analyses also appear in SQLite and the dashboard. Comparisons include all 12 rules plus ensemble and rank qualifying methods on validation data only, with the chosen method's later test results reported separately. No automatic live promotion occurs.

Runtime improvements include closed-candle validation, explicit unambiguous broker mapping, an account/mode-scoped persistent execution journal, broker account-currency sizing with costs, a 1% aggregate risk ceiling, restartable PAPER positions, SL/TP exits, optional break-even/trailing/partial exits, and an OS single-instance lock. Unknown sends suspend entries and are reconciled without resubmission. LIVE integration must be verified against the target broker; automated tests use isolated mock brokers.

New entries default to 0.5% equity risk. Stops respect ATR and confirmed structural invalidation, capped at 3 ATR. Net reward targets default to 1.5 (range), 2 (trend), and 2.5 (breakout), with lower acceptable candidates considered before nearby support/resistance. Fees, Bid/Ask and entry/exit slippage are included; the minimum net ratio is rechecked before sending and after fills. These targets are configurable research assumptions, not empirically optimal values. JSON reports include `trade_plan` and `reward_risk_policy`; comparisons include `reward_risk_sensitivity`. See [the Persian guide](STRATEGIES_FA.md).

## Architecture

```text
MT5 -> Market Data -> Indicators/Features -> Market Regime -> News Filter
    -> Signal Engine -> Confidence Engine -> Risk Manager -> Execution
    -> Position Management -> Database / Monitoring / Dashboard
```

The implementation includes MT5 connectivity, multi-timeframe data, indicator and market-structure analysis, price action, volume analysis, rule-based confidence, provider-based economic-news filtering, dynamic risk sizing using broker symbol specifications, paper/live execution gates, a bar backtester, walk-forward window generation, Monte Carlo analysis, ML feature/training/calibration modules, SQLite/PostgreSQL support, logs, tests and a Streamlit dashboard.

## Safety defaults

- `trading.mode: PAPER`
- `risk_per_trade: 0.005` (0.5% per entry; hard ceiling 1%)
- `max_total_risk: 0.01`
- `max_daily_loss: 0.015` (1.5%)
- `max_weekly_loss: 0.04` (4%)
- `max_drawdown: 0.08` (8%)
- `max_positions: 2`; `max_trades_per_day: 6`
- `max_consecutive_losses: 3`
- minimum confidence `0.70`
- mandatory SL/TP
- no martingale/grid/recovery sizing
- `kill_switch.flag` prevents new entries
- stale ticks prevent entries
- LIVE requires both `mode: LIVE` **and** `ENABLE_LIVE_TRADING=true`
- news is fail-closed by default when enabled but no valid calendar provider is configured

## Windows prerequisites

1. Windows 10/11 or Windows Server desktop session capable of running MetaTrader 5.
2. Python 3.12 x64.
3. MetaTrader 5 x64 installed and logged into a broker account.
4. Enable algorithmic trading in MT5 only after demo validation.

MetaTrader5 Python wheels are Windows-specific. Run the live/PAPER-MT5 integration on Windows.

## Installation

```powershell
cd C:\path\to\mt5_quant_trading_bot
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`:

```env
MT5_LOGIN=12345678
MT5_PASSWORD=YOUR_PASSWORD
MT5_SERVER=YourBroker-Server
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
DATABASE_URL=sqlite:///trading_bot.db
ENABLE_LIVE_TRADING=false
```

Never commit `.env`.

## Economic calendar / news

The default `news.provider: auto` uses Forex Factory's public weekly JSON export without an API key, plus official Federal Reserve monetary-policy/speech and ECB RSS feeds. The calendar refreshes at most hourly and RSS every five minutes, with persistent HTTP caches and failure backoff. Forex Factory supplies the current week's schedule/impact/forecast, not streaming actual release values. RSS importance is an explicit local risk rule; prose does not select trade direction. Required feed failures block entries for their mapped currencies. [راهنمای فارسی اخبار](NEWS_FA.md).

```powershell
.\.venv\Scripts\python.exe main.py news --symbol XAUUSD --output reports/news_status.json
```

This diagnostic fetches news and reports source health/publication times without initializing MT5 or enabling orders. In Docker use `docker compose exec trader python main.py news --symbol XAUUSD`. News caches persist in `/app/data/news_cache`; connection health is distinct from an article's publication time.

An existing `ECONOMIC_API_URL` overrides the public calendar in auto mode. The generic provider expects a list (or `{ "events": [...] }`) with:

```json
{
  "timestamp": "2026-09-12T12:30:00Z",
  "currency": "USD",
  "impact": "HIGH",
  "title": "CPI"
}
```

Optionally set `ECONOMIC_API_KEY`. If news filtering is enabled and `fail_closed: true`, missing or failed news data blocks new trades.

## Start PAPER mode

Ensure `config/settings.yaml` contains:

```yaml
trading:
  mode: PAPER
```

Then:

```powershell
python main.py health
python main.py run
```

The framework receives broker data but uses simulated fills in PAPER mode. Simulated equity, positions and exits persist in the database separately from LIVE. Quote polling cannot reconstruct every intratick SL/TP touch while stopped; PAPER fills are estimates. Daily/weekly risk baselines use UTC.

## Dashboard

```powershell
streamlit run dashboard\app.py
```

## Kill switch

Create the file below in the project directory:

```powershell
New-Item kill_switch.flag
```

New entries stop. Remove it only after the cause is understood:

```powershell
Remove-Item kill_switch.flag
```

## Backtest API example

```python
import pandas as pd
from backtest.engine import BacktestEngine

bars = pd.read_csv("bars.csv", parse_dates=["time"])
signals = pd.read_csv("signals.csv", parse_dates=["time"])
engine = BacktestEngine(initial_equity=10_000, spread=0.00010, commission_per_lot=7, slippage=0.00002)
trades, metrics = engine.run(bars, signals)
print(metrics)
```

Required signal columns: `time`, `direction`, `entry`, `sl`, `tp`, `contract_value_per_price`, `volume`. Same-bar SL+TP collisions are resolved conservatively as SL first. For production research, use tick/Bid/Ask data where possible.

`time` identifies the **completed source bar's opening timestamp**; execution occurs at the next available bar open. `entry` is informational and never used as a historical fill. OHLC is assumed to be Bid. BUY entry adds spread; SELL exits use Ask. Requested volume is capped by dynamic 1% sizing and remaining portfolio risk. Unclosed trades are liquidated at the end of the data. `volume` is optional and acts as an upper bound. `contract_value_per_price` must be a valid account-currency value per lot/one price unit; a constant conversion may be unsuitable for non-account-currency instruments.

Offline CLI (no MT5 connection):

```powershell
python main.py backtest --bars bars.csv --signals signals.csv --spread 0.00010 --slippage 0.00002 --commission 7
```

Generate technical signals from real exported history, or compare all 12 rules and ensemble with chronological 60/20/20 splits, cost and ATR-stop sensitivity and available 12/3/3-month walk-forward windows:

```powershell
python main.py export-bars --symbol EURUSD --days 365 --output reports/eurusd.csv
python main.py backtest --bars reports/eurusd.csv --family trend_pullback --contract-value 100000 --spread 0.00010 --slippage 0.00002 --commission 7
python main.py backtest --bars reports/eurusd.csv --compare --contract-value 100000 --spread 0.00010 --slippage 0.00002 --commission 7 --output reports/comparison.json
```

The contract value above is only an example for EURUSD with a USD account and a 100,000 EUR lot. Use actual specifications and costs. Generation requires at least 220 complete bars of every configured timeframe, with the CSV using the entry timeframe. Windows use preceding data for indicator warmup and never future bars. No automatic live strategy promotion occurs. News impact on historical results is **unvalidated** because historical news snapshots are not supplied.

## Walk-forward

```python
from backtest.walk_forward import build_windows
windows = build_windows(data.index, train_months=12, validation_months=3, test_months=3)
```

All windows should be reported. Do not select only the best one.

## Monte Carlo

```python
from backtest.monte_carlo import simulate
result = simulate(trade_returns, simulations=1000)
```

It reports return/drawdown distributions and a simple risk-of-ruin statistic.

## ML training

The ML subsystem is optional. Build features only from present/past bars, create labels without leaking future features, and use time-series splits.

```python
from ml.features import build_features, make_tp_before_sl_target
from ml.training import train_time_series, save_bundle

X = build_features(indicator_df)
y = make_tp_before_sl_target(indicator_df)
bundle = train_time_series(X, y, kind="logistic")
save_bundle(bundle)
```

For probability calibration, reserve a later chronological calibration set and use `ml.calibration.calibrate_prefit`. Do not allow ML confidence into live trading unless out-of-sample calibration is acceptable.

## Tests

```powershell
pytest -q
```

Risk tests verify that a $10,000 account at 1% configured risk produces a theoretical loss no larger than about $100 for different symbol specifications rather than using a single fixed pip formula.

## LIVE mode

Do not switch to LIVE merely because a backtest looks good. Validate on demo/PAPER first, preferably with at least the configured minimum sample size and walk-forward/out-of-sample checks.

To enable LIVE, both controls must be changed:

`config/settings.yaml`:

```yaml
trading:
  mode: LIVE
```

`.env`:

```env
ENABLE_LIVE_TRADING=true
```

Before doing so, verify: connection health, correct broker symbol names, fresh ticks, realistic spread/commission/slippage assumptions, stop-distance rules, margin, news-provider reliability, lot-size calculations, drawdown limits, duplicate-signal handling, and model calibration if ML is enabled.

## Symbol mapping

Broker aliases are listed in `config/symbols.yaml`. If your broker uses `XAUUSDm`, `EURUSD.a`, etc., update aliases and adapt runtime symbol resolution for your broker account.

Set `broker_symbol: EURUSDm` under the logical symbol to select an exact instrument. Multiple available aliases produce an error until an exact mapping is configured. The currency mapping remains attached to the logical symbol. Existing same-symbol positions block new entries in both netting and hedging accounts; manual positions are never modified.

The current workspace scans six instruments on the connected LiteFinance account: `EURUSD_o`, `GBPUSD_o`, `USDJPY_o`, `XAUUSD_o`, `NQ` (logical `NAS100`, Nasdaq 100) and `YM` (logical `US30`, Dow Jones). `python main.py symbols` lists actual terminal instruments without submitting orders. `trading.mt5_time_zone: Europe/Helsinki` normalizes the server wall-clock timestamps observed on this connection, including seasonal UTC+2/UTC+3 changes. For APIs that already supply UTC, set this to `UTC`. Health output reports individual trading permissions, normalized tick times and tick age; stale/future tick protection remains enabled.

Both US indices use the USD news filter and a shared us_equities risk factor, independent of the forex currency factors. They do not inherit the forex weekly schedule; fresh broker quotes remain mandatory. Economic USD surprise rules do not imply a forex-style direction for an index. Use `python main.py analyze --symbol NAS100 --output reports/nas100_analysis.json` or `--symbol US30 --output reports/us30_analysis.json` for an individual report. Restart a running trading engine after editing `config/symbols.yaml`; Docker mounts this file directly, so a configuration-only change needs no image rebuild.

## Important limitations

- No strategy guarantees profit.
- A 0.70 confidence threshold is a model/rule score, not an automatic 70% win rate.
- Real volume and market depth depend on the broker and instrument; the system must not fabricate unavailable data.
- The generic news provider requires a real external API URL/key.
- The included backtester is bar-based and intentionally conservative; institutional-grade research should use higher-fidelity tick/Bid/Ask data.
- Intrabar drawdown, historical swap and session-specific liquidity are not simulated. Ratios are unannualized per-trade statistics; the new chronological simulator reports bar-close mark-to-market drawdown and realized drawdown separately.
- Conservative FOK/IOC selection, partial entry fills and emergency protection are implemented but require broker demo validation. A partial entry is never topped up. Unresolved protection/partial-close outcomes block entries for operator review.
- Directional risk factors and aligned historical returns constrain concentration. Insufficient history uses explicit factor mapping; missing mapping blocks entries. This is not a fitted stress-correlation model.
- Generic JSON calendar/headline adapters require real authorized endpoints. `news.provider: tradingeconomics` uses the official calendar API with `ECONOMIC_API_KEY`; subscription coverage/latency must be verified. Unknown medium-impact release effects suspend entries by default. Numeric surprise rules are explicit configuration assumptions; prose sentiment cannot send orders or alter risk.
- Account changes during execution invalidate the connection. Do not delete the database/journal while positions or unresolved attempts exist. An alternate database, machine or copied project bypasses the local instance lock and must not run against the same account/magic concurrently.
- The 1% cap is planned risk. Gaps, actual slippage and broker execution can cause larger losses.

## Project tree

```text
mt5_quant_trading_bot/
├── main.py
├── config.py
├── requirements.txt
├── .env.example
├── config/
├── core/
├── analysis/
├── signals/
├── news/
├── ml/
├── backtest/
├── database/
├── monitoring/
├── dashboard/
├── tests/
└── utils/
```
