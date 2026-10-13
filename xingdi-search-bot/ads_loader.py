"""广告位 / 热搜配置加载。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_ADS: dict[str, Any] = {
    "top_ad": "",
    "service_links": [],
    "hot_searches": [],
}


class AdsConfig:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.data = dict(DEFAULT_ADS)
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            self.data = dict(DEFAULT_ADS)
            return
        with self.path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        self.data = {
            "top_ad": str(raw.get("top_ad") or ""),
            "service_links": list(raw.get("service_links") or []),
            "hot_searches": list(raw.get("hot_searches") or []),
        }

    @property
    def top_ad(self) -> str:
        return self.data["top_ad"]

    @property
    def service_links(self) -> list[dict]:
        return self.data["service_links"]

    @property
    def hot_searches(self) -> list[str]:
        return self.data["hot_searches"]
