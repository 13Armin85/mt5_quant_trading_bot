from __future__ import annotations
import time


class SymbolResolver:
    def __init__(self, client, symbol_config: dict) -> None:
        self.client=client; self.symbol_config=symbol_config
        self._catalog = None
        self._catalog_time = 0.0

    def catalog(self):
        if not hasattr(self.client, "symbols_get"):
            return None
        if self._catalog is None or time.monotonic() - self._catalog_time >= 30:
            symbols = self.client.symbols_get()
            if symbols is None:
                raise ValueError("Broker symbol catalog unavailable; check terminal connection")
            self._catalog = {s.name for s in symbols}
            self._catalog_time = time.monotonic()
        return self._catalog

    def resolve(self, logical: str) -> str:
        meta=self.symbol_config.get("symbols",{}).get(logical,{})
        explicit = meta.get("broker_symbol")
        candidates = [explicit] if explicit else meta.get("aliases", [logical])
        catalog = self.catalog()
        matches = list(dict.fromkeys(c for c in candidates if (c in catalog if catalog is not None else self.client.symbol_info(c) is not None)))
        if len(matches) > 1:
            raise ValueError(f"Ambiguous symbol {logical}: {matches}; set broker_symbol in config/symbols.yaml")
        if len(matches) == 1 and self.client.ensure_symbol(matches[0]):
            return matches[0]
        suggestions = sorted(name for name in catalog or () if any(alias.upper() in name.upper() for alias in [logical, *meta.get("aliases", [])]))
        detail = f"; available candidates: {suggestions}" if suggestions else ""
        raise ValueError(f"No accessible broker symbol for {logical}; set broker_symbol in config/symbols.yaml{detail}; run main.py symbols")
