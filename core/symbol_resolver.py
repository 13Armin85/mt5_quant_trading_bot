from __future__ import annotations


class SymbolResolver:
    def __init__(self, client, symbol_config: dict) -> None:
        self.client=client; self.symbol_config=symbol_config

    def resolve(self, logical: str) -> str | None:
        meta=self.symbol_config.get("symbols",{}).get(logical,{})
        for candidate in meta.get("aliases",[logical]):
            if self.client.symbol_info(candidate) is not None:
                self.client.ensure_symbol(candidate)
                return candidate
        return None
