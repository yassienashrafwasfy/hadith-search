"""Retrieval quality on the Quran sources: recall@k and MRR over (question, gold ayat) pairs.

    python scripts/quran_eval.py --questions data/quran/eval.jsonl --source quran \
        --source tafsir_muyassar --mode dense --mode hybrid

Each line of the questions file is `{"question": "...", "gold": [[surah, ayah], ...]}`. A question
is answered when any gold ayah is among the top k results. `dense` ranks by cosine alone, `hybrid`
is the production search (distinctive-word gate + RRF), so a question the gate rejects scores 0.
"""

import argparse
import json
import os
import sys
from collections.abc import Sequence

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

Ref = tuple[int, int]
KS = (1, 3, 8)


def first_hit_rank(ranked: Sequence[Ref], gold: set[Ref]) -> int | None:
    """1-based rank of the first gold ayah, or None."""
    for position, ref in enumerate(ranked, start=1):
        if ref in gold:
            return position
    return None


def metrics(ranked: Sequence[Sequence[Ref]], golds: Sequence[set[Ref]], ks=KS) -> dict[str, float]:
    """Recall@k and MRR over all questions (0.0 for an empty set)."""
    ranks = [first_hit_rank(r, g) for r, g in zip(ranked, golds, strict=True)]
    total = len(ranks) or 1
    result = {f"recall@{k}": sum(1 for r in ranks if r and r <= k) / total for k in ks}
    result["mrr"] = sum(1 / r for r in ranks if r) / total
    return result


def read_questions(path: str) -> list[tuple[str, set[Ref]]]:
    with open(path, encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return [(r["question"], {(int(s), int(a)) for s, a in r["gold"]}) for r in rows]


def ranked_refs(session, encoder, source: str, question: str, mode: str, k: int) -> list[Ref]:
    from quran.retrieval import search_source
    from quran.types import Filters
    from scripts.arabic_encoder import encoding_text

    cleaned = encoding_text(question)
    vector = [float(x) for x in encoder.encode([cleaned])[0]]
    query = question if mode == "hybrid" else None
    hits = search_source(session, source, vector, Filters(), k, query)
    return [(h.surah, h.ayah_from) for h in hits if h.surah is not None]


def evaluate(session, encoder, source: str, questions, mode: str, ks=KS) -> dict[str, float]:
    ranked = [ranked_refs(session, encoder, source, q, mode, max(ks)) for q, _ in questions]
    return metrics(ranked, [gold for _, gold in questions], ks)


def format_table(rows: dict[str, dict[str, float]]) -> str:
    columns = [f"recall@{k}" for k in KS] + ["mrr"]
    lines = ["run".ljust(32) + "".join(c.rjust(11) for c in columns)]
    for name, values in rows.items():
        lines.append(name.ljust(32) + "".join(f"{values[c]:11.3f}" for c in columns))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> dict[str, dict[str, float]]:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--questions", required=True)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--mode", action="append", choices=["dense", "hybrid"])
    args = parser.parse_args(argv)
    from database import get_sync_session
    from scripts import get_model

    questions, encoder = read_questions(args.questions), get_model()
    rows = {}
    with get_sync_session() as session:
        for source in args.source:
            for mode in args.mode or ["dense", "hybrid"]:
                rows[f"{source} {mode}"] = evaluate(session, encoder, source, questions, mode)
    print(format_table(rows))
    return rows


if __name__ == "__main__":
    main()
