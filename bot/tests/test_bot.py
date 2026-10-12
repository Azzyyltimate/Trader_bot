"""Tests hors ligne du bot : pipeline allégé (modèles scriptés, pas de réseau),
mise en forme, cache et quota.

    python -m pytest bot/tests -q
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from bot import formatting, pipeline
from bot.pipeline import AnalysisResult, LightPipeline
from bot.store import Store
from bot.synthesizer import Synthesis
from tests import test_graph_end_to_end as e2e
from tests.test_graph_end_to_end import ScriptedModel, offline  # noqa: F401 — fixture

SYNTH = Synthesis(
    market_trend="Tendance haussière modérée à moyen terme.",
    market_summary="m", sentiment_summary="Le sentiment est neutre.", news_summary="n",
    news_catalysts=["Résultats le 28 octobre", "Nouveau produit annoncé"],
    convergences=["Marché et News pointent vers une demande solide"],
    divergences=["Le sentiment des particuliers est plus prudent que les news"],
    watch_points=["Résistance à 105", "Publication de l'inflation"],
)


def _pipeline(monkeypatch, structured=True):
    model = ScriptedModel(structured=structured)
    monkeypatch.setitem(e2e.STRUCTURED, Synthesis, SYNTH)
    monkeypatch.setattr(pipeline, "create_tier_client", lambda config, tier, **k: e2e._Client(model))
    config = pipeline.pipeline_config()
    config["llm_provider"] = "openai"
    return LightPipeline(config), model


def test_graph_keeps_only_three_analysts_and_synthesizer(monkeypatch, offline):  # noqa: F811
    lp, _ = _pipeline(monkeypatch)
    nodes = set(lp.graph.get_graph().nodes) - {"__start__", "__end__"}
    assert nodes == {"Market Analyst", "Sentiment Analyst", "News Analyst", "Synthesizer"}


def test_run_end_to_end(monkeypatch, offline):  # noqa: F811
    lp, _ = _pipeline(monkeypatch)
    seen: list[str] = []
    result = lp.run("nvda", e2e.TRADE_DATE, on_progress=seen.append)

    assert result.ticker == "NVDA"
    assert set(seen) >= {"Market Analyst", "Sentiment Analyst", "News Analyst", "Synthesizer"}
    assert seen[-1] == "Synthesizer"
    assert result.reports["market_report"] and result.reports["news_report"]
    assert "Overall Sentiment" in result.reports["sentiment_report"]
    assert result.synthesis["watch_points"] == SYNTH.watch_points
    assert "Points à surveiller" in result.reports["synthesis_report"]
    # Chiffres calculés sur les prix (60 séances à 100,5 : pas de SMA 200).
    assert result.key_figures["close"] == 100.5
    assert "close_50_sma" in result.key_figures and "close_200_sma" not in result.key_figures
    # Aucun appel aux outils fondamentaux ni à la mémoire.
    assert not {"get_fundamentals", "get_balance_sheet"} & offline


def test_synthesizer_prompt_reads_reports_and_forbids_ratings(monkeypatch, offline):  # noqa: F811
    prompts = []
    lp, model = _pipeline(monkeypatch)
    real = e2e.STRUCTURED[Synthesis]
    monkeypatch.setattr(ScriptedModel, "with_structured_output",
                        lambda self, schema, **k: e2e.RunnableLambda(
                            lambda p: prompts.append(p) or (real if schema is Synthesis else e2e.STRUCTURED[schema])))
    lp = LightPipeline(lp.config)
    lp.run("NVDA", e2e.TRADE_DATE)
    synth_prompt = next(p for p in prompts if isinstance(p, str) and "neutral research synthesizer" in p)
    assert "Market Analyst report" in synth_prompt and "News Analyst report" in synth_prompt
    assert "Overall Sentiment" in synth_prompt          # le rapport de sentiment est bien injecté
    assert "Do NOT give any investment recommendation" in synth_prompt
    assert "Debate History" not in synth_prompt


def test_freetext_fallback(monkeypatch, offline):  # noqa: F811
    lp, _ = _pipeline(monkeypatch, structured=False)
    result = lp.run("NVDA", e2e.TRADE_DATE)
    assert result.synthesis is None and result.reports["synthesis_report"]
    message = formatting.summary_message(result)
    assert "4. Synthèse" in message and len(message) <= formatting.TELEGRAM_LIMIT


def _result(**over) -> AnalysisResult:
    data = {
        "ticker": "NVDA", "trade_date": "2026-10-12", "asset_type": "stock",
        "reports": {"market_report": "M", "news_report": "N", "synthesis_report": "S",
                 "sentiment_report": "**Overall Sentiment:** **Mildly Bullish** (Score: 6.2/10)\n"
                                     "**Confidence:** Medium\n\nnarratif"},
        "synthesis": SYNTH.model_dump(),
        "key_figures": {"date": "2026-10-09", "close": 1234.5, "change_pct": -1.234,
                     "rsi": 61.4, "close_50_sma": 1200.0, "close_200_sma": 1000.0},
    }
    data.update(over)
    return AnalysisResult(**data)


def test_summary_message_format():
    msg = formatting.summary_message(_result())
    for section in ("1. Marché", "2. Sentiment", "3. News", "4. Synthèse"):
        assert section in msg
    assert "1 234,50" in msg and "-1,23 %" in msg and "RSI 14 : 61" in msg
    assert "23,4 % au-dessus de la SMA 200" in msg
    assert "Plutôt haussier" in msg and "6,2/10" in msg and "confiance moyenne" in msg
    assert "pas un conseil" in msg


def test_message_escapes_html_and_fits():
    long = Synthesis(**{**SYNTH.model_dump(), "market_trend": "<script>&" + "x" * 5000})
    msg = formatting.summary_message(_result(synthesis=long.model_dump()))
    assert "<script>" not in msg and "&lt;script&gt;&amp;" in msg
    assert len(msg) <= formatting.TELEGRAM_LIMIT
    assert msg.count("<b>") == msg.count("</b>") and msg.count("<i>") == msg.count("</i>")


def test_cache_and_quota(tmp_path):
    store = Store(tmp_path, ZoneInfo("Europe/Paris"), max_per_day=2)
    result = _result(trade_date=store.today())
    assert store.get("nvda") is None
    store.put(result)
    assert store.get("nvda").reports == result.reports

    assert store.try_consume() and store.try_consume()
    assert not store.try_consume()
    store.refund()
    assert store.remaining() == 1


def test_rejects_unsafe_ticker(tmp_path):
    store = Store(tmp_path, ZoneInfo("Europe/Paris"), max_per_day=1)
    with pytest.raises(ValueError):
        store.get("../../etc")
