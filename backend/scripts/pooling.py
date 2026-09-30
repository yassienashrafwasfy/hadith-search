import json
import os
import sqlite3

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DB_PATH = os.path.join(DATA_DIR, "hadiths.db")
POOLING_MANIFEST_PATH = os.path.join(DATA_DIR, "pooling_manifest.json")
QUERIES_PATH = os.path.join(DATA_DIR, "queries.json")
QRELS_UNGRADED_PATH = os.path.join(DATA_DIR, "qrels_ungraded.json")

POOL_DEPTH_PER_SYSTEM = 50

from scripts.search import (
    bm25,
    bm25_semantic_rrf,
    bm25_with_expansion,
    final_search_pipeline,
    get_hadith,
    semantic_reranker,
    semantic_search_e5,
)

from scripts.loading import (
    get_english_inverted_index,
    get_arabic_inverted_index,
    get_document_lengths,
    get_english_embeddings,
    get_arabic_embeddings,
    get_hadith_ids,
    get_model,
)

print("Loading indices and embeddings...")
en_index = get_english_inverted_index()
ar_index = get_arabic_inverted_index()
doc_lengths = get_document_lengths()
en_embeddings = get_english_embeddings()
ar_embeddings = get_arabic_embeddings()
hadith_ids = get_hadith_ids()
model = get_model()

print("Loading hadiths into memory...")
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("SELECT id, English_Text, Arabic_Text FROM hadiths")
hadith_rows = cursor.fetchall()
conn.close()

hadith_texts_en = {int(row[0]): row[1] for row in hadith_rows if row[1]}
hadith_texts_ar = {int(row[0]): row[2] for row in hadith_rows if row[2]}
all_hadith_ids = {int(hid) for hid in hadith_ids}


def _index(language: str):
    return en_index if language == "EN" else ar_index


def _embeddings(language: str):
    return en_embeddings if language == "EN" else ar_embeddings


def _texts(language: str):
    return hadith_texts_en if language == "EN" else hadith_texts_ar


def _top_ids(scores: dict[int, float | int], limit: int) -> list[int]:
    return [int(hid) for hid in sorted(scores, key=scores.get, reverse=True)[:limit]]


def pool_query(query: str, language: str, per_system_size: int = POOL_DEPTH_PER_SYSTEM) -> tuple[list[int], dict]:
    index = _index(language)
    embeddings = _embeddings(language)
    texts = _texts(language)

    systems = {
        "BM25": lambda: bm25(query, language, index, doc_lengths),
        "BM25_ROCCHIO": lambda: bm25_with_expansion(query, language, index, doc_lengths, get_hadith),
        "COSINE_SIMILARITY": lambda: semantic_search_e5(
            query=query,
            language=language,
            model=model,
            corpus_embeddings_normed=embeddings,
            hadith_ids=hadith_ids,
            top_k=per_system_size,
        ),
        "BM25_SEMANTIC_RERANK": lambda: semantic_reranker(
            query=query,
            language=language,
            candidate_ids=list(bm25(query, language, index, doc_lengths).keys())[:500],
            model=model,
            embeddings=embeddings,
            hadith_ids=hadith_ids,
            top_k=per_system_size,
        ),
        "BM25_RRF": lambda: bm25_semantic_rrf(
            query=query,
            language=language,
            index=index,
            doc_lengths=doc_lengths,
            corpus_embeddings_normed=embeddings,
            hadith_ids=hadith_ids,
            model=model,
            candidate_k=500,
            top_k=per_system_size,
        ),
        "FINAL_PIPELINE": lambda: final_search_pipeline(
            query=query,
            language=language,
            index=index,
            doc_lengths=doc_lengths,
            embeddings=embeddings,
            hadith_ids=hadith_ids,
            model=model,
            eval_ids=all_hadith_ids,
            texts_dict=texts,
            candidate_k=1000,
            rerank_k=per_system_size,
            final_k=per_system_size,
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
            name: len(set(ids) - all_other_sets[name])
            for name, ids in system_outputs.items()
        },
        "system_errors": system_errors,
    }
    return sorted(pooled_ids), manifest


def run():
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
            "FINAL_PIPELINE",
        ],
        "queries": {},
    }

    print("\nPooling queries...")
    for qid, query_text in queries.items():
        language = qid[:2]

        print(f"  Processing {qid}...")
        pooled_ids, query_manifest = pool_query(query_text, language)

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
