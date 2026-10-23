from __future__ import annotations


def health_snapshot(client, feed, symbols: list[str]) -> dict:
    result = {"mt5": bool(client.health_check()), "symbols": {}}
    for symbol in symbols:
        try:
            result["symbols"][symbol] = {"tick_fresh": feed.is_tick_fresh(symbol), "spread_points": feed.spread_points(symbol)}
        except Exception as exc:
            result["symbols"][symbol] = {"tick_fresh": False, "error": str(exc)}
    return result
