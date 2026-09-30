import json
import os

from database import get_sync_session
from services import ranking

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
POOLING_MANIFEST_PATH = os.path.join(DATA_DIR, "pooling_manifest.json")
QUERIES_PATH = os.path.join(DATA_DIR, "queries.json")
QRELS_UNGRADED_PATH = os.path.join(DATA_DIR, "qrels_ungraded.json")

POOL_DEPTH_PER_SYSTEM = 50


def _top_ids(scores: dict[int, float | int], limit: int) -> list[int]:
    return [int(hid) for hid in sorted(scores, key=scores.get, reverse=True)[:limit]]


def pool_query(
    session,
    model,
    query: str,
    language: str,
    per_system_size: int = POOL_DEPTH_PER_SYSTEM,
) -> tuple[list[int], dict]:
    systems = {
        "BM25": lambda: ranking.bm25(session, query, language, limit=per_system_size),
        "BM25_ROCCHIO": lambda: ranking.bm25_prf(session, query, language),
        "COSINE_SIMILARITY": lambda: ranking.cosine_search(
            session, query, language, model, top_k=per_system_size
        ),
        "BM25_SEMANTIC_RERANK": lambda: ranking.semantic_rerank(
            session,
            query,
            language,
            list(ranking.bm25(session, query, language, limit=500)),
            model,
            top_k=per_system_size,
        ),
        "BM25_RRF": lambda: ranking.bm25_dense_rrf(
            session, query, language, model, candidate_k=500, top_k=per_system_size
        ),
    }

    pooled_ids: set[int] = set()
    system_outputs: dict[str, list[int]] = {}
    system_errors: dict[str, str] = {}

    for name, search_fn in systems.items():
        try:
            ids = _top_ids(search_fn(), per_system_size)
            system_outputs[name] = ids
            pooled_ids.update(ids)
        except Exception as exc:
            system_outputs[name] = []
            system_errors[name] = str(exc)

    all_other_sets = {
        name: set().union(*(set(v) for k, v in system_outputs.items() if k != name))
        for name in system_outputs
    }
    manifest = {
        "pool_size": len(pooled_ids),
        "system_contributions": {name: len(ids) for name, ids in system_outputs.items()},
        "system_unique_contributions": {
            name: len(set(ids) - all_other_sets[name]) for name, ids in system_outputs.items()
        },
        "system_errors": system_errors,
    }
    return sorted(pooled_ids), manifest


def run():
    from scripts import get_model

    print("Loading queries.json...")
    with open(QUERIES_PATH, encoding="utf-8") as f:
        queries = json.load(f)

    qrels_ungraded = {}
    distributions = {}
    pooling_manifest = {
        "pool_depth_per_system": POOL_DEPTH_PER_SYSTEM,
        "systems": [
            "BM25",
            "BM25_ROCCHIO",
            "COSINE_SIMILARITY",
            "BM25_SEMANTIC_RERANK",
            "BM25_RRF",
        ],
        "queries": {},
    }

    print("\nLoading model...")
    model = get_model()
    print("\nPooling queries...")
    for qid, query_text in queries.items():
        language = qid[:2]

        print(f"  Processing {qid}...")
        with get_sync_session() as session:
            pooled_ids, query_manifest = pool_query(session, model, query_text, language)

        qrels_ungraded[qid] = pooled_ids
        distributions[qid] = len(pooled_ids)
        pooling_manifest["queries"][qid] = query_manifest
        print(f"    -> {len(pooled_ids)} hadiths pooled")

    print("\n" + "=" * 50)
    print("DISTRIBUTIONS")
    print("=" * 50)
    for qid, count in distributions.items():
        print(f"{qid}: {count} hadiths")

    manifest_path = POOLING_MANIFEST_PATH
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(pooling_manifest, f, ensure_ascii=False, indent=2)

    failed = {
        qid: data["system_errors"]
        for qid, data in pooling_manifest["queries"].items()
        if data["system_errors"]
    }
    if failed and os.getenv("ALLOW_PARTIAL_POOLING", "0") != "1":
        raise RuntimeError(
            "Pooling failed for one or more systems. "
            f"Inspect {manifest_path}. Set ALLOW_PARTIAL_POOLING=1 to write a partial pool."
        )

    output_path = QRELS_UNGRADED_PATH
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(qrels_ungraded, f, ensure_ascii=False, indent=2)

    print(f"\nSaved to: {output_path}")
    print(f"Manifest saved to: {manifest_path}")


if __name__ == "__main__":
    run()
