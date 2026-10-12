"""Test en ligne de commande du pipeline allégé, sans Telegram.

    python -m bot.cli AAPL                 analyse du jour, affiche le message Telegram
    python -m bot.cli AAPL --date 2026-10-09
    python -m bot.cli AAPL --no-cache      ignore et n'écrit pas le cache

Les rapports complets sont aussi écrits dans bot/data/cli/<TICKER>_<date>/.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time

from .config import data_dir, pipeline_config, timezone
from .formatting import STEPS, summary_message
from .store import Store


def _as_plain(html: str) -> str:
    return re.sub(r"</?[bi]>", "", html).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#x27;", "'")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker")
    parser.add_argument("--date", help="AAAA-MM-JJ (défaut : aujourd'hui)")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    # La console Windows n'est pas toujours en UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")

    store = Store(data_dir(), timezone(), max_per_day=10**6)  # pas de quota en CLI
    day = args.date or store.today()
    ticker = args.ticker.strip().upper()

    result = None if args.no_cache else store.get(ticker, day)
    cached = result is not None
    if not cached:
        from .pipeline import LightPipeline  # import lourd : seulement si on calcule

        config = pipeline_config()
        print(f"Fournisseur : {config['llm_provider']} | rapide : {config['quick_think_llm']} "
              f"| profond : {config['deep_think_llm']} | langue : {config['output_language']}")
        start = time.monotonic()

        def progress(node: str) -> None:
            if node in STEPS:
                print(f"  ✅ {STEPS[node]} ({time.monotonic() - start:.0f} s)", flush=True)

        print(f"Analyse de {ticker} au {day}…")
        result = LightPipeline(config).run(ticker, day, on_progress=progress)
        if not args.no_cache:
            store.put(result)

    out = data_dir() / "cli" / f"{result.ticker}_{result.trade_date}"
    out.mkdir(parents=True, exist_ok=True)
    for key, text in result.reports.items():
        (out / f"{key}.md").write_text(text, encoding="utf-8")

    message = summary_message(result, cached=cached)
    print("\n" + "=" * 60 + f"\nMessage Telegram ({len(message)} caractères) :\n" + "=" * 60)
    print(_as_plain(message))
    print("=" * 60)
    print("Longueur des rapports : " + ", ".join(f"{k} {len(v)}" for k, v in result.reports.items()))
    print(f"Rapports complets : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
