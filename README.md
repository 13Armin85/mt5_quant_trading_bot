# MT5 Quant Trading Bot

A modular Python 3.12+ trading framework for MetaTrader 5. The project is deliberately **PAPER-first**. A confidence score is not a guarantee of win rate or profit, and `LIVE` execution is blocked unless it is explicitly enabled.

## Architecture

```text
MT5 -> Market Data -> Indicators/Features -> Market Regime -> News Filter
    -> Signal Engine -> Confidence Engine -> Risk Manager -> Execution
    -> Position Management -> Database / Monitoring / Dashboard
```

The implementation includes MT5 connectivity, multi-timeframe data, indicator and market-structure analysis, price action, volume analysis, rule-based confidence, provider-based economic-news filtering, dynamic risk sizing using broker symbol specifications, paper/live execution gates, a bar backtester, walk-forward window generation, Monte Carlo analysis, ML feature/training/calibration modules, SQLite/PostgreSQL support, logs, tests and a Streamlit dashboard.

## Safety defaults

- `trading.mode: PAPER`
- `risk_per_trade: 0.01`
- `max_total_risk: 0.03`
- `max_daily_loss: 0.03`
- `max_weekly_loss: 0.06`
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

The code does not invent a provider. Set `ECONOMIC_API_URL` to a real JSON calendar API that you are authorized to use. The generic provider expects a list (or `{ "events": [...] }`) with:

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

The framework receives live broker data but uses simulated fills in PAPER mode.

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

## Important limitations

- No strategy guarantees profit.
- A 0.70 confidence threshold is a model/rule score, not an automatic 70% win rate.
- Real volume and market depth depend on the broker and instrument; the system must not fabricate unavailable data.
- The generic news provider requires a real external API URL/key.
- The included backtester is bar-based and intentionally conservative; institutional-grade research should use higher-fidelity tick/Bid/Ask data.
- Trading sessions, broker holidays, swap, partial fills, and broker-specific filling modes may need adaptation to the target broker.

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
