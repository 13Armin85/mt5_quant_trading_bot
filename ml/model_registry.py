from __future__ import annotations

from pathlib import Path
from ml.model import ModelBundle


class ModelRegistry:
    def __init__(self, directory: str = "models") -> None:
        self.directory=Path(directory); self.directory.mkdir(exist_ok=True)

    def latest(self) -> ModelBundle | None:
        files=sorted(self.directory.glob("*.joblib"), key=lambda p:p.stat().st_mtime, reverse=True)
        return ModelBundle.load(str(files[0])) if files else None
