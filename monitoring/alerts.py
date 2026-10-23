from __future__ import annotations

import httpx
from loguru import logger


class AlertManager:
    def __init__(self, env) -> None:
        self.env = env

    def send(self, text: str) -> None:
        logger.info("ALERT {}", text)
        if self.env.discord_webhook_url:
            try: httpx.post(self.env.discord_webhook_url, json={"content": text}, timeout=8).raise_for_status()
            except Exception as exc: logger.error("Discord alert failed: {}", exc)
        if self.env.telegram_bot_token and self.env.telegram_chat_id:
            url = f"https://api.telegram.org/bot{self.env.telegram_bot_token}/sendMessage"
            try: httpx.post(url, json={"chat_id": self.env.telegram_chat_id, "text": text}, timeout=8).raise_for_status()
            except Exception as exc: logger.error("Telegram alert failed: {}", exc)
