from __future__ import annotations

from pathlib import Path
from loguru import logger


def configure_logging() -> None:
    Path("logs").mkdir(exist_ok=True)
    logger.add("logs/trading_{time:YYYY-MM-DD}.log", rotation="00:00", retention="30 days", enqueue=True, level="INFO")
