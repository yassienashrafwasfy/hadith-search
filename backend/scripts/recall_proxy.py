"""Recall@k of the Arabic semantic methods with no human labels (proxy tests).

    python -m scripts.recall_proxy [--k 3 8] [--n 1000] [--seed 42] [--out FILE]

There are no relevance judgments in the repository, so two automatic tests stand in for them.
Both use the real search systems of the app (`services.retrieval.SYSTEMS`, Arabic queries).

1. Known item: the query is the first half of a hadith's Arabic text and the relevant set is every
   hadith whose text starts with that same half (copies of one hadith in several books count).
   Recall@k is then 1 when a relevant hadith is in the top k, else 0. This rewards close wording.
2. Chapter: the query is an Arabic chapter title and the relevant set is every hadith under that
   title. These sets have 40 to 600 hadiths, so plain recall@k would be at most k / size. The
   score is the capped recall: hits in the top k divided by min(k, size). Hit rate (at least one
   relevant hadith in the top k) is reported next to it.

Neither test is a substitute for human relevance labels.
"""

import argparse
import json
import os
import random
import time

import numpy as np
from sqlalchemy import select

from database import get_sync_session
from models import Hadith
from scripts.arabic_encoder import encoding_text
from scripts.loading import get_model
from services.retrieval import SYSTEMS, SearchContext

METHODS = ("cosine-similarity", "semantic-rerank", "semantic-rrf")
MIN_WORDS = 12  # shorter texts make a half too short to be a query
DEFAULT_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "recall_proxy.json"
)


def ranked_ids(scores: dict[int, float]) -> list[int]:
    return [hid for hid, _ in sorted(scores.items(), key=lambda item: -item[1])]


def mean_with_interval(values: list[float], seed: int = 0) -> dict:
    """Mean and a 95% bootstrap interval over queries."""
    data = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = rng.choice(data, size=(1000, len(data))).mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return {"mean": float(data.mean()), "ci95": [float(low), float(high)], "queries": len(data)}


def known_item_queries(texts: dict[int, str], n: int, seed: int):
    """(query, relevant ids) for n hadiths sampled with a fixed seed."""
    long_enough = sorted(hid for hid, text in texts.items() if len(text.split()) >= MIN_WORDS)
    queries = []
    for hid in random.Random(seed).sample(long_enough, min(n, len(long_enough))):
        words = texts[hid].split()
        query = " ".join(words[: len(words) // 2])
        relevant = {other for other, text in texts.items() if text.startswith(query)}
        queries.append((query, relevant))
    return queries


def chapter_queries(rows) -> list[tuple[str, set[int]]]:
    by_title: dict[str, set[int]] = {}
    for hid, title in rows:
        if title and title.strip():
            by_title.setdefault(encoding_text(title), set()).add(hid)
    return sorted(by_title.items())


def evaluate(system, ctx, queries, ks, capped: bool) -> dict:
    per_k = {k: {"recall": [], "hit": []} for k in ks}
    for query, relevant in queries:
        ids = ranked_ids(system.run(ctx, query, "AR"))
        for k in ks:
            found = len(set(ids[:k]) & relevant)
            denominator = min(k, len(relevant)) if capped else len(relevant)
            per_k[k]["recall"].append(found / denominator)
            per_k[k]["hit"].append(1.0 if found else 0.0)
    return {
        f"k={k}": {
            ("capped_recall" if capped else "recall"): mean_with_interval(v["recall"]),
            "hit_rate": mean_with_interval(v["hit"]),
        }
        for k, v in per_k.items()
    }


def run(ks: list[int], n: int, seed: int, out: str) -> dict:
    with get_sync_session() as session:
        rows = session.execute(
            select(Hadith.id, Hadith.Arabic_Matn, Hadith.Chapter_Title_Arabic)
        ).all()
        texts = {hid: encoding_text(matn) for hid, matn, _ in rows if matn}
        known = known_item_queries(texts, n, seed)
        chapters = chapter_queries([(hid, title) for hid, _, title in rows])
        model = get_model()
        ctx = SearchContext(session=session, model=lambda: model)
        report = {
            "seed": seed,
            "ks": ks,
            "corpus_hadiths": len(rows),
            "known_item_queries": len(known),
            "chapter_queries": len(chapters),
            "methods": {},
        }
        for slug in METHODS:
            start = time.time()
            report["methods"][slug] = {
                "known_item": evaluate(SYSTEMS[slug], ctx, known, ks, capped=False),
                "chapter": evaluate(SYSTEMS[slug], ctx, chapters, ks, capped=True),
            }
            print(f"{slug}: {time.time() - start:.0f}s")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def print_report(report: dict) -> None:
    for test, metric in (("known_item", "recall"), ("chapter", "capped_recall")):
        print(
            f"\n{test} ({report[test + '_queries' if test == 'chapter' else 'known_item_queries']} queries)"
        )
        for slug, result in report["methods"].items():
            cells = []
            for k in report["ks"]:
                block = result[test][f"k={k}"]
                m = block[metric]
                cells.append(
                    f"{metric}@{k} {m['mean']:.3f} [{m['ci95'][0]:.3f}-{m['ci95'][1]:.3f}]"
                )
            print(f"  {slug:18s} " + "  ".join(cells))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--k", type=int, nargs="+", default=[3, 8])
    parser.add_argument("--n", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    print_report(run(args.k, args.n, args.seed, args.out))


if __name__ == "__main__":
    main()
