from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent


def _yaml(name: str) -> dict[str, Any]:
    with (ROOT / "config" / name).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    mt5_login: int | None = Field(default=None, alias="MT5_LOGIN")
    mt5_password: str | None = Field(default=None, alias="MT5_PASSWORD")
    mt5_server: str | None = Field(default=None, alias="MT5_SERVER")
    mt5_path: str | None = Field(default=None, alias="MT5_PATH")
    news_api_key: str | None = Field(default=None, alias="NEWS_API_KEY")
    economic_api_key: str | None = Field(default=None, alias="ECONOMIC_API_KEY")
    news_api_url: str | None = Field(default=None, alias="NEWS_API_URL")
    economic_api_url: str | None = Field(default=None, alias="ECONOMIC_API_URL")
    database_url: str = Field(default="sqlite:///trading_bot.db", alias="DATABASE_URL")
    enable_live_trading: bool = Field(default=False, alias="ENABLE_LIVE_TRADING")
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID")
    discord_webhook_url: str | None = Field(default=None, alias="DISCORD_WEBHOOK_URL")


class AppConfig(BaseModel):
    settings: dict[str, Any]
    symbols: dict[str, Any]
    strategy: dict[str, Any]
    env: EnvSettings

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
