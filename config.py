from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent


def _yaml(name: str) -> dict[str, Any]:
    with (ROOT / "config" / name).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    mt5_login: int | None = Field(default=None, alias="MT5_LOGIN")
    mt5_password: str | None = Field(default=None, alias="MT5_PASSWORD", repr=False)
    mt5_server: str | None = Field(default=None, alias="MT5_SERVER")
    mt5_path: str | None = Field(default=None, alias="MT5_PATH")
    news_api_key: str | None = Field(default=None, alias="NEWS_API_KEY", repr=False)
    economic_api_key: str | None = Field(default=None, alias="ECONOMIC_API_KEY", repr=False)
    news_api_url: str | None = Field(default=None, alias="NEWS_API_URL")
    economic_api_url: str | None = Field(default=None, alias="ECONOMIC_API_URL")
    news_cache_dir: str = Field(default=str(ROOT / ".docker" / "data" / "news_cache"), alias="NEWS_CACHE_DIR")
    database_url: str = Field(default="sqlite:///trading_bot.db", alias="DATABASE_URL")
    enable_live_trading: bool = Field(default=False, alias="ENABLE_LIVE_TRADING")
    mt5_bridge_url: str | None = Field(default=None, alias="MT5_BRIDGE_URL")
    mt5_bridge_token_file: str = Field(default=str(ROOT / ".docker" / "bridge.token"), alias="MT5_BRIDGE_TOKEN_FILE")
    runtime_lock_path: str = Field(default=str(ROOT / ".bot.lock"), alias="RUNTIME_LOCK_PATH")
    kill_switch_path: str | None = Field(default=None, alias="KILL_SWITCH_PATH")
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID")
    discord_webhook_url: str | None = Field(default=None, alias="DISCORD_WEBHOOK_URL")

    @field_validator("mt5_login", mode="before")
    @classmethod
    def blank_login(cls, value):
        return None if value == "" else value


class AppConfig(BaseModel):
    settings: dict[str, Any]
    symbols: dict[str, Any]
    strategy: dict[str, Any]
    env: EnvSettings

    def validate_runtime(self) -> None:
        import math
        from zoneinfo import ZoneInfo
        ZoneInfo(str(self.get("trading.mt5_time_zone", "UTC")))
        if str(self.get("trading.mode", "PAPER")).upper() not in {"PAPER", "LIVE", "BACKTEST"}:
            raise ValueError("trading.mode must be PAPER, LIVE or BACKTEST")
        for key in ("risk_per_trade", "max_total_risk"):
            value = float(self.get(f"trading.{key}", .01))
            if not math.isfinite(value) or not 0 < value <= .01:
                raise ValueError(f"trading.{key} must be > 0 and <= 0.01")
        for key, default in (("poll_seconds", 2), ("max_tick_age_seconds", 15), ("max_spread_points", 40)):
            value = float(self.get(f"trading.{key}", default))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"trading.{key} must be positive")
        for key in ("commission_per_lot", "slippage_points"):
            value = float(self.get(f"trading.{key}", 0))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"trading.{key} must be nonnegative")
        if int(self.get("trading.default_bars", 700)) < 220:
            raise ValueError("trading.default_bars must be >= 220")
        tfs = self.get("strategy.timeframes", {})
        if set(tfs) != {"macro", "trend", "setup", "entry"}:
            raise ValueError("strategy.timeframes requires macro, trend, setup and entry")
        if any(tf not in {"M1", "M5", "M15", "M30", "H1", "H4", "D1"} for tf in tfs.values()):
            raise ValueError("Unsupported strategy timeframe")
        if not self.symbols.get("symbols"):
            raise ValueError("Configure at least one symbol")
        for logical,meta in self.symbols["symbols"].items():
            schedule = meta.get("quote_schedule")
            if schedule:
                if schedule.get("type") not in {"litefinance_forex","weekend_only"}:
                    raise ValueError(f"Unsupported quote_schedule for {logical}")
                ZoneInfo(schedule["timezone"])
        from signals.strategies import FAMILIES
        if self.get("strategy.family", "ensemble") not in (*FAMILIES, "auto", "ensemble"):
            raise ValueError("Unsupported strategy.family")
        enabled = self.get("strategy.enabled_strategies", list(FAMILIES))
        if not isinstance(enabled,list) or not enabled or len(set(enabled))!=len(enabled) or any(name not in FAMILIES for name in enabled):
            raise ValueError("enabled_strategies must contain unique supported strategy names")
        if self.get("strategy.family", "ensemble") in FAMILIES and self.get("strategy.family") not in enabled:
            raise ValueError("Explicit strategy.family must be enabled")
        for key, default in (("minimum_score",.65),("rule_weight",.70)):
            value = float(self.get(f"strategy.selection.{key}",default))
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"strategy.selection.{key} must be between 0 and 1")
        if not 1<=int(self.get("strategy.selection.minimum_groups",1))<=4:
            raise ValueError("strategy.selection.minimum_groups must be 1..4")
        component_weights = self.get("strategy.selection.component_weights",{})
        components = {"regime_fit","higher_timeframes","oscillators","volume","price_confirmation","volatility","entry_trigger"}
        if component_weights and (set(component_weights)!=components or any(not math.isfinite(float(v)) or float(v)<0 for v in component_weights.values()) or sum(component_weights.values())<=0):
            raise ValueError("selection.component_weights must contain the seven supported components with finite nonnegative weights and positive total")
        if int(self.get("strategy.breakout_lookback", 20)) < 2:
            raise ValueError("breakout_lookback must be >= 2")
        for name in ("stop_atr_multiple", "pullback_atr"):
            value = float(self.get(f"strategy.{name}", 1.5))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"strategy.{name} must be positive")
        for key,default in (("min_rr",1.5),("preferred_rr",2.0)):
            value=float(self.get(f"trading.{key}",default))
            if not math.isfinite(value) or value<=0:
                raise ValueError(f"trading.{key} must be positive")
        rr=self.get("strategy.reward_risk",{})
        minimum=float(rr.get("minimum_net_rr",1.5))
        if not math.isfinite(minimum) or minimum<float(self.get("trading.min_rr",1.5)):
            raise ValueError("reward_risk.minimum_net_rr must be finite and >= trading.min_rr")
        for key,default in (("range_target",1.5),("trend_target",2.0),("breakout_target",2.5)):
            value=float(rr.get(key,default))
            if not math.isfinite(value) or value<minimum or value>10:
                raise ValueError(f"reward_risk.{key} must be between the minimum net RR and 10")
        candidates=rr.get("candidates",[1.5,2,2.5,3])
        if not isinstance(candidates,list) or not candidates or any(not math.isfinite(float(v)) or not minimum<=float(v)<=10 for v in candidates):
            raise ValueError("reward_risk.candidates must be finite net RR values between minimum and 10")
        buffer=float(rr.get("structure_buffer_atr",.1));maximum=float(rr.get("max_stop_atr",3))
        if not math.isfinite(buffer) or buffer<0 or not math.isfinite(maximum) or maximum<float(self.get("strategy.stop_atr_multiple",1.5)):
            raise ValueError("Invalid reward_risk structure buffer or maximum stop distance")
        window=int(rr.get("swing_window",3));lookback=int(rr.get("structure_lookback",100))
        if window<1 or lookback<2*window+1 or lookback>min(300,int(self.get("trading.default_bars",700))):
            raise ValueError("Invalid reward_risk swing window/lookback")
        fraction = float(self.get("position_management.partial_close_fraction", .5))
        if not math.isfinite(fraction) or not 0 < fraction < 1:
            raise ValueError("partial_close_fraction must be between 0 and 1")
        for key, default in (("max_daily_loss", .03), ("max_weekly_loss", .06), ("max_drawdown", .10), ("min_confidence", .70)):
            value = float(self.get(f"trading.{key}", default))
            if not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"trading.{key} must be > 0 and <= 1")
        for key in ("max_positions", "max_trades_per_day", "reconnect_attempts", "max_consecutive_losses"):
            if int(self.get(f"trading.{key}", 3)) < 1:
                raise ValueError(f"trading.{key} must be positive")
        for key in ("block_before_minutes", "block_after_minutes", "poll_seconds", "stale_after_minutes", "timeout_seconds"):
            value = float(self.get(f"news.{key}", 30))
            if not math.isfinite(value) or value < 0 or (key.endswith("seconds") and value == 0):
                raise ValueError(f"news.{key} invalid")
        if self.get("news.provider", "generic") not in {"auto", "forexfactory", "generic", "tradingeconomics"}:
            raise ValueError("Unsupported news.provider")
        snapshot_age = float(self.get("news.max_snapshot_age_seconds", 180))
        if not math.isfinite(snapshot_age) or snapshot_age < float(self.get("news.poll_seconds", 60)):
            raise ValueError("news.max_snapshot_age_seconds must be finite and >= news.poll_seconds")
        if int(self.get("news.calendar_refresh_seconds", 3600)) < 3600:
            raise ValueError("Public weekly calendar refresh must be at least 3600 seconds")
        headlines = self.get("news.headlines", {})
        if int(headlines.get("refresh_seconds", 300)) < 300:
            raise ValueError("Official RSS refresh must be at least 300 seconds")
        if headlines.get("enabled") and not self.env.news_api_url and not headlines.get("sources"):
            raise ValueError("Configure at least one official RSS source")
        from urllib.parse import urlsplit
        for source in headlines.get("sources", []):
            address = urlsplit(source.get("url", ""))
            if (not source.get("name") or address.scheme != "https" or not address.hostname
                    or address.username or address.password or not source.get("currencies")
                    or any(c not in {"USD","EUR","GBP","JPY","CHF","CAD","AUD","NZD","CNY","XAU"} for c in source["currencies"])
                    or source.get("default_impact", "LOW") not in {"LOW", "MEDIUM", "HIGH"}):
                raise ValueError("Invalid official RSS source configuration")
        for key, value in self.get("strategy.indicators", {}).items():
            if int(value) < 2 or int(value) > int(self.get("trading.default_bars", 700)) - 20:
                raise ValueError(f"strategy.indicators.{key} outside history limits")
        indicators = self.get("strategy.indicators",{})
        for low, high, a, b in (("ema_fast","ema_mid",20,50),("ema_mid","ema_slow",50,200),("macd_fast","macd_slow",12,26),("ichimoku_fast","ichimoku_base",9,26),("ichimoku_base","ichimoku_span",26,52)):
            if int(indicators.get(low,a)) >= int(indicators.get(high,b)):
                raise ValueError(f"strategy.indicators.{low} must be less than {high}")

    def get(self, path: str, default: Any = None) -> Any:
        current: Any = self.settings
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current


@lru_cache(maxsize=1)
def load_config() -> AppConfig:
    return AppConfig(
        settings=_yaml("settings.yaml"),
        symbols=_yaml("symbols.yaml"),
        strategy=_yaml("strategy.yaml"),
        env=EnvSettings(),
    )
