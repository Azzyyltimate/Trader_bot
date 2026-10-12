"""Pipeline allégé : Market, Sentiment et News en parallèle, puis le synthétiseur.

Réutilise les briques du package d'origine sans le modifier : les fabriques
d'analystes, ``_analyst_graph`` (chaque analyste dans son propre sous-graphe,
avec sa limite de tours d'outils), les clients LLM par niveau et l'identité
d'instrument. Pas de débat, de Trader, de risque, de Portfolio Manager ni de
mémoire des décisions.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any

from langgraph.graph import END, START, StateGraph
from stockstats import wrap

from cli.prompts import detect_asset_type
from tradingagents.agents import (
    create_market_analyst,
    create_news_analyst,
    create_sentiment_analyst,
)
from tradingagents.agents.context import build_instrument_context, resolve_instrument_identity
from tradingagents.agents.state import AgentState
from tradingagents.dataflows.config import run_config_context
from tradingagents.dataflows.symbols import safe_ticker_component
from tradingagents.dataflows.vendors.yahoo.snapshot import _verified_rows
from tradingagents.graph.analyst_execution import build_analyst_execution_plan
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.setup import _analyst_graph
from tradingagents.llm_clients import create_tier_client

from .config import ANALYSTS, pipeline_config
from .synthesizer import create_synthesizer

logger = logging.getLogger(__name__)

SYNTHESIZER = "Synthesizer"
REPORT_KEYS = ("market_report", "sentiment_report", "news_report", "synthesis_report")


class LightState(AgentState):
    synthesis: Annotated[dict | None, "Champs structurés du synthétiseur, None en repli texte libre"]
    synthesis_report: Annotated[str, "Synthèse complète en markdown"]


@dataclass
class AnalysisResult:
    ticker: str
    trade_date: str
    asset_type: str
    reports: dict[str, str]          # market_report, sentiment_report, news_report, synthesis_report
    synthesis: dict | None           # champs de Synthesis, ou None
    key_figures: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data: dict) -> AnalysisResult:
        return cls(**data)


ProgressCallback = Callable[[str], None]


class LightPipeline:
    """Construit le graphe une fois ; ``run`` peut ensuite être appelé à volonté."""

    def __init__(self, config: dict | None = None):
        self.config = config or pipeline_config()
        quick = create_tier_client(self.config, "quick").get_llm()
        deep = create_tier_client(self.config, "deep").get_llm()

        factories = {
            "market": lambda: create_market_analyst(quick),
            "social": lambda: create_sentiment_analyst(quick),
            "news": lambda: create_news_analyst(quick),
        }
        plan = build_analyst_execution_plan(ANALYSTS)
        self.analyst_nodes = [spec.agent_node for spec in plan.specs]

        workflow = StateGraph(LightState)
        for spec in plan.specs:
            workflow.add_node(spec.agent_node,
                              _analyst_graph(spec, factories[spec.key](), self.config["max_tool_rounds"]))
            workflow.add_edge(START, spec.agent_node)
        workflow.add_node(SYNTHESIZER, create_synthesizer(deep))
        # Le synthétiseur attend que les trois analystes aient rendu leur rapport.
        workflow.add_edge(self.analyst_nodes, SYNTHESIZER)
        workflow.add_edge(SYNTHESIZER, END)
        self.graph = workflow.compile()
        self.propagator = Propagator(max_recur_limit=self.config["max_recur_limit"])

    def run(self, ticker: str, trade_date: str, on_progress: ProgressCallback | None = None) -> AnalysisResult:
        ticker = safe_ticker_component(ticker.strip().upper())
        asset_type = detect_asset_type(ticker).value
        notify = on_progress or (lambda _node: None)

        # Toute la course, outils de données compris, lit la config du bot.
        context = run_config_context(self.config)
        instrument_context = context.run(
            lambda: build_instrument_context(ticker, asset_type, resolve_instrument_identity(ticker), trade_date))
        state = self.propagator.create_initial_state(
            ticker, trade_date, asset_type=asset_type, instrument_context=instrument_context)

        final: dict = {}
        stream = context.run(self.graph.stream, state,
                             config={"recursion_limit": self.config["max_recur_limit"]},
                             stream_mode=["tasks", "values"])
        try:
            while (step := context.run(next, stream, None)) is not None:
                mode, chunk = step
                if mode == "values":
                    final = chunk
                elif mode == "tasks" and "result" in chunk:
                    if chunk.get("error"):
                        raise RuntimeError(f"{chunk['name']} a échoué : {chunk['error']}")
                    notify(chunk["name"])
        finally:
            context.run(stream.close)

        return AnalysisResult(
            ticker=ticker,
            trade_date=trade_date,
            asset_type=asset_type,
            reports={key: final.get(key) or "" for key in REPORT_KEYS},
            synthesis=final.get("synthesis"),
            key_figures=context.run(key_figures, ticker, trade_date),
        )


def key_figures(ticker: str, trade_date: str) -> dict[str, Any]:
    """Chiffres clés calculés en Python sur les mêmes données que le snapshot
    vérifié de l'analyste Market : jamais recopiés par un LLM."""
    try:
        df = _verified_rows(ticker, trade_date)
        stock = wrap(df.copy())
        last = df.iloc[-1]
        figures: dict[str, Any] = {
            "date": last["Date"].strftime("%Y-%m-%d"),
            "close": float(last["Close"]),
        }
        if len(df) >= 2:
            prev = float(df.iloc[-2]["Close"])
            figures["change_pct"] = (figures["close"] / prev - 1) * 100
        for name in ("rsi", "close_50_sma", "close_200_sma"):
            value = stock[name].iloc[-1]
            # Une SMA 200 sur moins de 200 séances n'est pas une SMA 200.
            window = {"close_50_sma": 50, "close_200_sma": 200}.get(name, 14)
            if value == value and len(df) >= window:  # NaN != NaN
                figures[name] = float(value)
        return figures
    except Exception as exc:  # noqa: BLE001 — les chiffres sont un plus, pas un prérequis
        logger.warning("Chiffres clés indisponibles pour %s : %s", ticker, exc)
        return {}
