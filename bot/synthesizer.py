"""Synthétiseur : remplace le Research Manager dans le pipeline allégé.

Dérivé de ``tradingagents/agents/managers/research_manager.py``, mais il lit
directement les trois rapports d'analystes (pas d'historique de débat) et ne
choisit aucune note : il résume, compare et liste les points à surveiller.
La décision reste à l'utilisateur.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from tradingagents.agents.context import get_instrument_context_from_state, get_language_instruction
from tradingagents.agents.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_structured,
)


class Synthesis(BaseModel):
    """Synthèse neutre des trois analyses, sans recommandation."""

    market_trend: str = Field(
        description="The market trend in ONE sentence (direction, strength, timeframe). No numbers needed.",
    )
    market_summary: str = Field(
        description="Two to four sentences summarising the Market Analyst's report.",
    )
    sentiment_summary: str = Field(
        description="Two to four sentences summarising the Sentiment Analyst's report: overall mood and what drives it.",
    )
    news_summary: str = Field(
        description="Two to four sentences summarising the News Analyst's report.",
    )
    news_catalysts: list[str] = Field(
        description="The 2 to 5 most important events or catalysts from the news report, one short line each, with dates when known.",
    )
    convergences: list[str] = Field(
        description="Points where at least two of the three analyses agree, one short line each, naming which analyses.",
    )
    divergences: list[str] = Field(
        description="Points where the analyses disagree or contradict each other, one short line each, naming which analyses. Empty if none.",
    )
    watch_points: list[str] = Field(
        description="2 to 5 concrete things to monitor next (levels, dates, events, data releases), one short line each.",
    )


def render_synthesis(s: Synthesis) -> str:
    """Le rapport de synthèse complet, en markdown, pour le bouton « Synthèse »."""
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) or "- (aucun)"

    return "\n".join([
        f"**Tendance de marché** : {s.market_trend}",
        "",
        "## Résumé Marché", s.market_summary, "",
        "## Résumé Sentiment", s.sentiment_summary, "",
        "## Résumé News", s.news_summary, "",
        "## Catalyseurs", bullets(s.news_catalysts), "",
        "## Convergences", bullets(s.convergences), "",
        "## Divergences", bullets(s.divergences), "",
        "## Points à surveiller", bullets(s.watch_points),
    ])


def create_synthesizer(llm):
    structured_llm = bind_structured(llm, Synthesis, "Synthesizer")

    def synthesizer_node(state) -> dict:
        instrument_context = get_instrument_context_from_state(state)

        prompt = f"""You are a neutral research synthesizer. Three analysts have each written a report on the same instrument. Your job is to summarise each report, compare them, and list what to monitor. The reader makes their own decision.

{instrument_context}

**Strict rules:**
- Do NOT give any investment recommendation, rating, price target, position size, or Buy / Sell / Hold / Overweight / Underweight call, explicitly or implicitly.
- Describe and compare; never advise.
- Use only facts stated in the reports below. Do not invent numbers. If a report is empty or says data was unavailable, say so.

---

**Market Analyst report:**
{state.get("market_report") or "(empty)"}

---

**Sentiment Analyst report:**
{state.get("sentiment_report") or "(empty)"}

---

**News Analyst report:**
{state.get("news_report") or "(empty)"}

---

{NO_EXTERNAL_TOOLS}""" + get_language_instruction()

        result = invoke_structured(structured_llm, prompt, "Synthesizer")
        if result is not None:
            return {"synthesis": result.model_dump(), "synthesis_report": render_synthesis(result)}
        # Repli texte libre (fournisseur sans sortie structurée) : la synthèse
        # n'a pas de champs, le message Telegram affichera le texte brut.
        text = llm.invoke(prompt + "\n\nStructure your answer with the headings: "
                          "Market trend, Summaries, Catalysts, Convergences, Divergences, Watch points.").content
        return {"synthesis": None, "synthesis_report": text}

    return synthesizer_node
