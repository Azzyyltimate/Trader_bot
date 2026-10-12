"""Cache des analyses du jour et quota journalier, en fichiers JSON.

    data/cache/AAAA-MM-JJ/TICKER.json   un AnalysisResult par ticker et par jour
    data/usage.json                     {"AAAA-MM-JJ": nombre d'analyses lancées}

Un résultat servi depuis le cache ne compte pas dans le quota.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingagents.dataflows.symbols import safe_ticker_component

from .pipeline import AnalysisResult


class Store:
    def __init__(self, data_dir: Path, tz: ZoneInfo, max_per_day: int):
        self.data_dir = Path(data_dir)
        self.tz = tz
        self.max_per_day = max_per_day
        self._lock = threading.Lock()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def today(self) -> str:
        return datetime.now(self.tz).strftime("%Y-%m-%d")

    # --- cache -------------------------------------------------------------

    def _cache_path(self, ticker: str, day: str) -> Path:
        return self.data_dir / "cache" / day / f"{safe_ticker_component(ticker.upper())}.json"

    def get(self, ticker: str, day: str | None = None) -> AnalysisResult | None:
        path = self._cache_path(ticker, day or self.today())
        if not path.exists():
            return None
        return AnalysisResult.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def put(self, result: AnalysisResult) -> None:
        path = self._cache_path(result.ticker, result.trade_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    # --- quota -------------------------------------------------------------

    def _usage_path(self) -> Path:
        return self.data_dir / "usage.json"

    def _read_usage(self) -> dict:
        try:
            return json.loads(self._usage_path().read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def used_today(self) -> int:
        return int(self._read_usage().get(self.today(), 0))

    def remaining(self) -> int:
        return max(0, self.max_per_day - self.used_today())

    def try_consume(self) -> bool:
        """Réserve une analyse sur le quota du jour ; False si le quota est épuisé."""
        with self._lock:
            usage = self._read_usage()
            day = self.today()
            if usage.get(day, 0) >= self.max_per_day:
                return False
            usage[day] = usage.get(day, 0) + 1
            usage = dict(sorted(usage.items())[-30:])  # on ne garde que les 30 derniers jours
            self._usage_path().write_text(json.dumps(usage, indent=2), encoding="utf-8")
            return True

    def refund(self) -> None:
        """Rend une analyse réservée qui a échoué : une panne ne doit pas coûter de quota."""
        with self._lock:
            usage = self._read_usage()
            day = self.today()
            if usage.get(day, 0) > 0:
                usage[day] -= 1
                self._usage_path().write_text(json.dumps(usage, indent=2), encoding="utf-8")
