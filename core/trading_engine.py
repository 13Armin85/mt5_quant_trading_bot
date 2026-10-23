from __future__ import annotations

import time
from pathlib import Path

from loguru import logger

from analysis.indicators import add_indicators
from analysis.market_structure import analyze_structure
from analysis.regime_detector import detect_regime
from analysis.volume import analyze_volume
from signals.signal_engine import SignalEngine
from utils.types import Direction, OrderRequest, TradingMode


class TradingEngine:
    def __init__(self, app_cfg, client, feed, risk_manager, execution, repository, news_filter) -> None:
        self.cfg = app_cfg
        self.client = client
        self.feed = feed
        self.risk = risk_manager
        self.execution = execution
        self.repository = repository
        self.news_filter = news_filter
        self.signal_engine = SignalEngine(app_cfg)
        self.running = True

    def run_forever(self) -> None:
        poll = float(self.cfg.get("trading.poll_seconds", 2))
        symbols = list(self.cfg.symbols.get("symbols", {}).keys())
        logger.info("Trading engine started symbols={}", symbols)
        while self.running:
            for symbol in symbols:
                try:
                    self.process_symbol(symbol)
                except Exception as exc:
                    logger.exception("{} processing failed: {}", symbol, exc)
            time.sleep(poll)

    def process_symbol(self, logical_symbol: str):
        if Path(self.cfg.get("trading.kill_switch_path", "kill_switch.flag")).exists():
            logger.warning("Kill switch active")
            return None
        symbol = logical_symbol
        if not self.feed.is_tick_fresh(symbol):
            logger.warning("{} rejected: stale/missing tick", symbol)
            return None
        frames: dict[str, object] = {}
        tf_cfg = self.cfg.get("strategy.timeframes", {})
        for role, tf in tf_cfg.items():
            df = self.feed.bars(symbol, tf, int(self.cfg.get("trading.default_bars", 700)))
            if df.empty:
                logger.warning("{} rejected: no {} data", symbol, tf)
                return None
            frames[role] = add_indicators(df, self.cfg.get("strategy.indicators", {}))
        entry_df = frames["entry"]
        regime = detect_regime(frames["trend"], self.cfg.get("strategy.filters", {}))
        structure = analyze_structure(frames["setup"])
        volume = analyze_volume(frames["setup"], self.cfg.get("strategy.indicators", {}))
        news_ok, news_reason, news_score = self.news_filter.evaluate(symbol)
        spread = self.feed.spread_points(symbol)
        signal = self.signal_engine.generate(symbol, frames, regime, structure, volume, news_ok, news_reason, news_score, spread)
        self.repository.save_signal(signal)
        if signal.direction == Direction.HOLD:
            return signal
        if not news_ok:
            return signal
        account = self.client.account_info()
        equity = float(getattr(account, "equity", self.cfg.get("trading.paper_starting_balance", 10000))) if account else float(self.cfg.get("trading.paper_starting_balance", 10000))
        allowed, reason = self.risk.can_trade(equity, self.repository.open_risk_fraction(equity))
        if not allowed:
            logger.warning("{} rejected: {}", symbol, reason)
            return signal
        info = self.client.symbol_info(symbol)
        if info is None:
            return signal
        lot = self.risk.calculate_volume(symbol, signal.entry_price, signal.stop_loss, equity, info)
        if lot <= 0:
            logger.warning("{} rejected: calculated volume below broker minimum", symbol)
            return signal
        tick = self.client.symbol_info_tick(symbol)
        price = float(tick.ask if signal.direction == Direction.BUY else tick.bid)
        req = OrderRequest(symbol, signal.direction, lot, price, signal.stop_loss, signal.take_profit, int(self.cfg.get("trading.max_deviation_points", 20)), comment=signal.signal_id or "quant-bot")
        result = self.execution.execute(req)
        self.repository.save_execution(signal, lot, result)
        logger.info("{} {} confidence={:.2f} result={}", symbol, signal.direction.value, signal.confidence, result.message)
        return signal
