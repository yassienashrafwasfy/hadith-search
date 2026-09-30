"""Shared evaluation pipeline: score every retrieval system on the eval pool and save stats.

Used by `scripts.evaluation` (baseline) and `scripts.finetune_eval` (fine-tuned embeddings);
they differ only in where the outputs go.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime

from database import get_sync_session
from services import ranking

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

BM25_CANDIDATES = 500
COSINE_TOP_K = 20
METRIC_NAMES = ("AP", "RR", "P@20", "R@20", "F1@20")


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_eval_inputs():
    """(query_ids, queries, relevant_list, languages) from queries.json + qrels_graded.json."""
    queries_data = load_json(os.path.join(DATA_DIR, "queries.json"))
    qrels_graded = load_json(os.path.join(DATA_DIR, "qrels_graded.json"))
    query_ids = list(queries_data.keys())
    relevant_list = [
        {int(k): v for k, v in qrels_graded.get(qid, {}).get("grades", {}).items()}
        for qid in query_ids
    ]
    languages = ["AR" if qid.startswith("AR") else "EN" for qid in query_ids]
    return query_ids, list(queries_data.values()), relevant_list, languages


def eval_pool_ids(relevant_list):
    """Graded ids plus every pooled candidate: the closed pool systems are scored against."""
    eval_ids = {hid for grades in relevant_list for hid in grades}
    pool_path = os.path.join(DATA_DIR, "qrels_ungraded.json")
    if os.path.exists(pool_path):
        eval_ids.update(int(hid) for ids in load_json(pool_path).values() for hid in ids)
    return eval_ids


class EvalResources:
    """A database session, the E5 model and the eval pool shared by every system."""

    def __init__(self, eval_ids, session):
        from scripts import get_model

        self.session = session
        self.eval_ids = eval_ids
        print("Loading model...")
        self.model = get_model()
        print(f"eval pool size:   {len(eval_ids)}")


def _bm25_candidates(res, query, language):
    """BM25 ranking cut to the eval pool, best first."""
    return list(
        ranking.bm25(res.session, query, language, restrict=res.eval_ids, limit=BM25_CANDIDATES)
    )


def _bi_encoder(res, query, language):
    return ranking.semantic_rerank(
        res.session,
        query,
        language,
        _bm25_candidates(res, query, language),
        res.model,
        top_k=BM25_CANDIDATES,
    )


def _rrf(res, query, language):
    return ranking.bm25_dense_rrf(res.session, query, language, res.model, restrict=res.eval_ids)


_SIMULATED = {"bi-encoder": _bi_encoder, "rrf": _rrf}


def simulated_pipeline(res, query, language, model_type):
    if model_type not in _SIMULATED:
        raise ValueError(f"Unknown model_type: {model_type}")
    return _SIMULATED[model_type](res, query, language)


def _lexical_systems(res):
    session = res.session
    return {
        "BM25": lambda q, lang: ranking.bm25(session, q, lang),
        "TF_IDF": lambda q, lang: ranking.tf_idf(session, q, lang),
        "Term Overlap": lambda q, lang: ranking.term_overlap(session, q, lang),
        "BM25_ROCCHIO": lambda q, lang: ranking.bm25_prf(session, q, lang),
        "BM25_TF_IDF": lambda q, lang: ranking.bm25_tfidf_hybrid(session, q, lang),
        "BM25_TF_IDF_ROCCHIO": lambda q, lang: ranking.hybrid_prf(session, q, lang),
    }


def _dense_systems(res):
    return {
        "COSINE_SIMILARITY": lambda q, lang: ranking.dense_search(
            res.session,
            ranking.encode_query(res.model, q, lang),
            lang,
            COSINE_TOP_K,
            restrict=res.eval_ids,
        )
    }


def _hybrid_systems(res):
    simulated = {
        "BM25_SEMANTIC_RERANK": "bi-encoder",
        "BM25_RRF": "rrf",
    }
    systems = {
        name: (lambda q, lang, kind=kind: simulated_pipeline(res, q, lang, kind))
        for name, kind in simulated.items()
    }
    return systems


def build_systems(res):
    return {**_lexical_systems(res), **_dense_systems(res), **_hybrid_systems(res)}


def _metrics_block(row, k):
    metrics = {name: float(row[name]) for name in METRIC_NAMES}
    metrics[f"nDCG@{k}"] = float(row[f"nDCG@{k}"])
    return {"Metrics": metrics}


def _system_block(df, query_ids, queries, k):
    block = {
        qid: {"Query Text": text, **_metrics_block(df.loc[qid], k)}
        for qid, text in zip(query_ids, queries)
    }
    block["MEAN"] = _metrics_block(df.loc["MEAN"], k)
    return block


def evaluate_systems(systems, eval_inputs, eval_ids, k):
    from scripts.evaluation import _filter_and_rank, evaluate_system

    query_ids, queries, relevant_list, languages = eval_inputs
    all_results = {}
    for system_name, search_fn in systems.items():
        print(f"\nEvaluating [{system_name}]...")
        ranked = [
            _filter_and_rank(search_fn(query, lang), eval_ids)
            for query, lang in zip(queries, languages)
        ]
        retrieved = [[doc_id for doc_id, _ in per_query] for per_query in ranked]
        df = evaluate_system(query_ids, retrieved, relevant_list, k)
        print(df.to_string())
        all_results[system_name] = _system_block(df, query_ids, queries, k)
    return all_results


@dataclass(frozen=True)
class OutputNames:
    """File names (inside data/) for the results JSON, stats JSON and LaTeX table."""

    results: str
    stats: str
    table: str


def _stamp(path, timestamp):
    root, ext = os.path.splitext(path)
    return f"{root}_{timestamp}{ext}"


def _save_with_archive(path, timestamp, write, label):
    stamped = _stamp(path, timestamp)
    write(path)
    write(stamped)
    print(f"{label:18s} -> {path}")
    print(f"{label + ' (archived)':18s} -> {stamped}")


def save_outputs(names, all_results, stats_output, latex_table):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def write_text(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(latex_table)

    print()
    for name, write in (
        (names.results, lambda p: write_json(p, all_results)),
        (names.stats, lambda p: write_json(p, stats_output)),
        (names.table, write_text),
    ):
        _save_with_archive(
            os.path.join(DATA_DIR, name), timestamp, write, os.path.splitext(name)[0]
        )


def print_best_systems(stats_output, filtered_results):
    print("\nBest systems per metric (graded queries only):")
    for metric, best_sys in stats_output["summary"]["best_systems"].items():
        mean_val = filtered_results[best_sys]["MEAN"]["Metrics"].get(metric, 0.0)
        print(f"  {metric:12s}: {best_sys} ({float(mean_val):.4f})")


def run_pipeline(names, k=20):
    """Evaluate all systems against the graded qrels and write results, stats and LaTeX."""
    from scripts.stats_tests import filter_graded_queries, generate_latex_table, run_analysis

    eval_inputs = load_eval_inputs()
    query_ids, _queries, relevant_list, _languages = eval_inputs
    eval_ids = eval_pool_ids(relevant_list)

    with get_sync_session() as session:
        systems = build_systems(EvalResources(eval_ids, session))
        all_results = evaluate_systems(systems, eval_inputs, eval_ids, k)

    graded_qids = {qid for qid, rel in zip(query_ids, relevant_list) if rel}
    filtered = filter_graded_queries(all_results, graded_qids)
    print(f"\n{'='*60}\nStatistical Analysis ({len(graded_qids)} graded queries)\n{'='*60}")
    stats_output = run_analysis(filtered, baseline="BM25", k=k)
    latex = generate_latex_table(filtered, stats_output["pairwise_tests"], baseline="BM25", k=k)

    save_outputs(names, all_results, stats_output, latex)
    print_best_systems(stats_output, filtered)
