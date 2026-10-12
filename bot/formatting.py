"""Mise en forme des messages Telegram (HTML) à partir d'un AnalysisResult."""

from __future__ import annotations

import re
from html import escape

from .pipeline import SYNTHESIZER, AnalysisResult

TELEGRAM_LIMIT = 4096

# Nœuds du graphe -> libellés de progression, dans l'ordre d'affichage.
STEPS = {
    "Market Analyst": "Marché",
    "Sentiment Analyst": "Sentiment",
    "News Analyst": "News",
    SYNTHESIZER: "Synthèse",
}

# Boutons « rapport complet » : clé courte (callback_data) -> (libellé, clé du rapport).
REPORT_BUTTONS = {
    "m": ("📈 Marché", "market_report"),
    "s": ("💬 Sentiment", "sentiment_report"),
    "n": ("📰 News", "news_report"),
    "y": ("🧩 Synthèse", "synthesis_report"),
}

_BANDS_FR = {
    "Bullish": "Haussier",
    "Mildly Bullish": "Plutôt haussier",
    "Neutral": "Neutre",
    "Mixed": "Partagé",
    "Mildly Bearish": "Plutôt baissier",
    "Bearish": "Baissier",
}
_CONFIDENCE_FR = {"low": "faible", "medium": "moyenne", "high": "élevée"}

# En-tête écrit par render_sentiment_report (labels en anglais, posés par le code).
_SENTIMENT_RE = re.compile(r"\*\*Overall Sentiment:\*\*\s*\*\*(?P<band>[^*]+)\*\*\s*\(Score:\s*(?P<score>[\d.]+)/10\)")
_CONFIDENCE_RE = re.compile(r"\*\*Confidence:\*\*\s*(?P<conf>\w+)")

DISCLAIMER = ("<i>Éléments d'analyse uniquement, générés par IA. Ce n'est pas un conseil "
              "d'investissement : la décision vous appartient.</i>")


def progress_text(ticker: str, done: set[str], error: str | None = None) -> str:
    lines = [f"⏳ <b>Analyse de {escape(ticker)} en cours…</b>", ""]
    analysts_done = all(node in done for node in list(STEPS)[:3])
    for node, label in STEPS.items():
        if node in done:
            mark = "✅"
        elif node == SYNTHESIZER and not analysts_done:
            mark = "▫️"
        else:
            mark = "⏳"
        lines.append(f"{mark} {label}")
    if error:
        lines += ["", f"❌ {escape(error)}"]
    else:
        lines += ["", "<i>Compter quelques minutes.</i>"]
    return "\n".join(lines)


def parse_sentiment(report: str) -> dict:
    """Humeur, score et confiance lus dans l'en-tête structuré du Sentiment Analyst."""
    out: dict = {}
    if m := _SENTIMENT_RE.search(report or ""):
        out["band"] = m["band"].strip()
        out["score"] = float(m["score"])
    if m := _CONFIDENCE_RE.search(report or ""):
        out["confidence"] = m["conf"].lower()
    return out


def _num(value: float, decimals: int = 2) -> str:
    """Nombre au format français : 1 234,56."""
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def _bullets(items, limit: int = 5) -> str:
    items = [i for i in (items or []) if i and i.strip()][:limit]
    return "\n".join(f"• {escape(i.strip())}" for i in items) or "• (rien de notable)"


def _market_lines(fig: dict) -> list[str]:
    lines = []
    if "close" in fig:
        # Pendant la séance, la dernière ligne Yahoo est un cours intraday, pas une clôture.
        line = f"• Dernier cours ({fig['date']}) : {_num(fig['close'])}"
        if "change_pct" in fig:
            line += f" ({'+' if fig['change_pct'] >= 0 else ''}{_num(fig['change_pct'])} % vs clôture précédente)"
        lines.append(line)
    if "rsi" in fig:
        lines.append(f"• RSI 14 : {_num(fig['rsi'], 0)}")
    for key, label in (("close_200_sma", "SMA 200"), ("close_50_sma", "SMA 50")):
        if key in fig and "close" in fig:
            gap = (fig["close"] / fig[key] - 1) * 100
            side = "au-dessus" if gap >= 0 else "en dessous"
            lines.append(f"• Cours {_num(abs(gap), 1)} % {side} de la {label} ({_num(fig[key])})")
            break  # 3 chiffres maximum
    return lines or ["• Chiffres vérifiés indisponibles"]


def summary_message(result: AnalysisResult, cached: bool = False) -> str:
    s = result.synthesis or {}
    sent = parse_sentiment(result.reports.get("sentiment_report", ""))

    head = f"📊 <b>{escape(result.ticker)}</b> — analyse du {result.trade_date}"
    if cached:
        head += "\n<i>(résultat déjà calculé aujourd'hui)</i>"

    market = [s.get("market_trend") and escape(s["market_trend"]) or "<i>Tendance non disponible.</i>"]
    market += _market_lines(result.key_figures)

    if sent:
        mood = _BANDS_FR.get(sent.get("band", ""), sent.get("band", "?"))
        score = f"{_num(sent['score'], 1)}/10" if "score" in sent else "?/10"
        conf = _CONFIDENCE_FR.get(sent.get("confidence", ""), sent.get("confidence", "?"))
        sentiment = [f"Humeur : <b>{escape(mood)}</b> — score {score} — confiance {escape(conf)}"]
    else:
        sentiment = ["<i>Score de sentiment non disponible.</i>"]
    if s.get("sentiment_summary"):
        sentiment.append(escape(s["sentiment_summary"]))

    if s:
        news = _bullets(s.get("news_catalysts"))
        synthesis = "\n".join([
            "<b>Convergences</b>", _bullets(s.get("convergences"), 4),
            "<b>Divergences</b>", _bullets(s.get("divergences"), 4),
            "<b>À surveiller</b>", _bullets(s.get("watch_points"), 5),
        ])
    else:
        # Repli texte libre : pas de champs, on montre le début du texte.
        news = "<i>Voir le rapport News complet.</i>"
        synthesis = escape(result.reports.get("synthesis_report", "")[:1500])

    sections = [
        head,
        "<b>1. Marché</b>\n" + "\n".join(market),
        "<b>2. Sentiment</b>\n" + "\n".join(sentiment),
        "<b>3. News</b>\n" + news,
        "<b>4. Synthèse</b>\n" + synthesis,
        DISCLAIMER,
    ]
    return _fit("\n\n".join(sections))


def _fit(text: str, limit: int = TELEGRAM_LIMIT) -> str:
    """Coupe proprement un message trop long (rare : la synthèse est concise)."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 300]  # place pour la mention de troncature et l'avertissement
    # Couper en fin de ligne si elle est proche, sinon en pleine ligne ;
    # jamais au milieu d'une balise ou d'une entité HTML.
    newline = cut.rfind("\n")
    cut = cut[:newline] if newline > len(cut) - 500 else re.sub(r"(<[^>]*|&[#\w]*)$", "", cut) + "…"
    # Refermer les balises restées ouvertes.
    for tag in ("i", "b"):
        if cut.count(f"<{tag}>") > cut.count(f"</{tag}>"):
            cut += f"</{tag}>"
    return cut + "\n\n<i>… message tronqué, voir les rapports complets.</i>\n\n" + DISCLAIMER


def report_title(key: str) -> str:
    return {v[1]: v[0] for v in REPORT_BUTTONS.values()}.get(key, key)
