"""Réglages du bot, lus depuis le .env à la racine du dépôt.

Les clés (token Telegram, clé du fournisseur LLM) ne vivent que dans le .env.
Les réglages TRADINGAGENTS_* du .env sont appliqués par
``build_default_config`` du package d'origine ; le bot n'impose que la langue.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Importé après load_dotenv : DEFAULT_CONFIG lit l'environnement à l'import.
from tradingagents.default_config import build_default_config  # noqa: E402

# Les trois analystes du pipeline allégé ("social" = Sentiment Analyst).
ANALYSTS = ("market", "social", "news")


def pipeline_config() -> dict:
    """La config TradingAgents du bot : défauts + .env, sortie en français."""
    config = build_default_config()
    config["output_language"] = os.getenv("BOT_OUTPUT_LANGUAGE", "French")
    return config


@dataclass(frozen=True)
class BotSettings:
    token: str
    allowed_user_id: int
    max_analyses_per_day: int
    timezone: ZoneInfo
    data_dir: Path


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} manquant dans le .env ({ROOT / '.env'})")
    return value


def bot_settings() -> BotSettings:
    """Les réglages propres au bot Telegram ; échoue tôt si l'un manque."""
    return BotSettings(
        token=_require("TELEGRAM_BOT_TOKEN"),
        allowed_user_id=int(_require("TELEGRAM_ALLOWED_USER_ID")),
        max_analyses_per_day=int(os.getenv("MAX_ANALYSES_PER_DAY", "5")),
        timezone=timezone(),
        data_dir=data_dir(),
    )


def timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("BOT_TIMEZONE", "Europe/Paris"))


def data_dir() -> Path:
    return Path(os.getenv("BOT_DATA_DIR") or ROOT / "bot" / "data")
