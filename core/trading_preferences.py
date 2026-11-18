"""Bounded, account-scoped trading preferences and persistent daily guard."""
from datetime import timezone
import math
import hashlib
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo


DEFAULTS = {"activity": "active", "daily_enabled": False, "profit_target": 5.0, "loss_limit": 2.0}


def preferences_path(directory, scope):
    return Path(directory) / ("preferences-" + hashlib.sha256(scope.encode()).hexdigest()[:24] + ".json")


def read_preferences(directory, scope, fallback=None):
    path = preferences_path(directory, scope)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        saved = fallback or dict(DEFAULTS)
    return validate_preferences(saved)


def write_preferences(directory, scope, payload):
    preferences = validate_preferences(payload)
    path = preferences_path(directory, scope)
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".preferences-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(preferences, handle)
            handle.flush()
            os.fsync(handle.fileno())
        # The account portal owns this file. Its private group grants the
        # matching engine read access without making the trade journal writable.
        temporary.chmod(0o640)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return preferences


def validate_preferences(payload):
    if not isinstance(payload, dict) or set(payload) != set(DEFAULTS):
        raise ValueError("تنظیمات ارسال‌شده معتبر نیست.")
    if payload["activity"] not in {"normal", "active"} or type(payload["daily_enabled"]) is not bool:
        raise ValueError("حالت معامله یا فعال‌سازی محدودیت روزانه معتبر نیست.")
    result = dict(payload)
    for name in ("profit_target", "loss_limit"):
        value = payload[name]
        if type(value) not in (int, float) or not math.isfinite(value) or not .01 <= value <= 100000:
            raise ValueError("هدف سود و سقف ضرر باید بین ۰٫۰۱ و ۱۰۰٬۰۰۰ واحد ارز حساب باشند.")
        result[name] = float(value)
    return result


def activity_config(cfg, activity):
    result = cfg.model_copy(deep=True)
    if activity == "active":
        # More frequent closed-bar opportunities, with unchanged stop, cost,
        # exposure, news and loss controls. Never switch research profiles on.
        result.settings["strategy"]["timeframes"] = {"macro":"H1", "trend":"M15", "setup":"M5", "entry":"M1"}
        result.settings["strategy"]["selection"]["minimum_score"] = .60
        result.settings["trading"]["min_confidence"] = .65
        result.settings["trading"]["max_trades_per_day"] = 24
    return result


class DailyGuard:
    """Equity change since the first observed sample of each Tehran calendar day.

    The persisted baseline/latch never resets on logout, restart or settings edit.
    This covers the selected account's equity, including fees, floating results
    and external deposits/withdrawals. It is not a reconstruction of midnight.
    """
    def __init__(self, repo, scope, clock):
        self.repo, self.key, self.clock = repo, scope + ":daily_guard", clock
        self.policy = dict(DEFAULTS)
        self.state = repo.get_state(self.key) or {}

    def update(self, equity):
        equity = float(equity)
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError("Daily guard equity unavailable")
        now = self.clock()
        day = now.astimezone(ZoneInfo("Asia/Tehran")).date().isoformat()
        if self.state.get("day") != day:
            self.state = {"day":day, "baseline_equity":equity,
                "started_at":now.astimezone(timezone.utc).isoformat(), "halt":None}
            self.repo.set_state(self.key, self.state)
        pnl = equity - float(self.state["baseline_equity"])
        if self.policy["daily_enabled"] and not self.state.get("halt"):
            halt = ("DAILY_LOSS_AMOUNT" if pnl <= -self.policy["loss_limit"] else
                "DAILY_PROFIT_TARGET" if pnl >= self.policy["profit_target"] else None)
            if halt:
                self.state.update(halt=halt, reached_at=now.astimezone(timezone.utc).isoformat())
                self.repo.set_state(self.key, self.state)
        return {**self.state, "pnl":pnl, "enabled":self.policy["daily_enabled"],
            "profit_target":self.policy["profit_target"], "loss_limit":self.policy["loss_limit"],
            "remaining_loss":max(0., self.policy["loss_limit"] + pnl),
            "halt":self.state.get("halt") if self.policy["daily_enabled"] else None}

    def remaining(self, equity):
        status = self.update(equity)
        if not status["enabled"]:
            return math.inf
        return 0. if status["halt"] else status["remaining_loss"]

    def allowed(self, equity):
        status = self.update(equity)
        return (False, status["halt"]) if status["halt"] else (True, "Daily guard clear")
