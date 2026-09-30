"""Shared evaluation pipeline: score every retrieval system on the eval pool and save stats.

Used by `scripts.evaluation` (baseline) and `scripts.finetune_eval` (fine-tuned embeddings);
they differ only in where the outputs go.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from database import read_hadiths_df
from models import Hadith

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

BM25_CANDIDATES = 500
CROSS_ENCODER_CANDIDATES = 100
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
    """Indices, embeddings (restricted to the eval pool) and texts shared by every system."""

    def __init__(self, eval_ids):
        from scripts import (
            get_arabic_embeddings,
            get_arabic_inverted_index,
            get_document_lengths,
            get_english_embeddings,
            get_english_inverted_index,
            get_hadith_ids,
            get_model,
        )

        hadiths_df = read_hadiths_df(Hadith.id, Hadith.English_Text, Hadith.Arabic_Text)
        hadiths_df = hadiths_df.set_index("id")
        self.eval_ids = eval_ids
        self.texts = {
            "EN": hadiths_df["English_Text"].to_dict(),
            "AR": hadiths_df["Arabic_Text"].to_dict(),
        }
        print("Loading indexes and models...")
        self.indexes = {"EN": get_english_inverted_index(), "AR": get_arabic_inverted_index()}
        self.doc_lengths = get_document_lengths()
        self.model = get_model()
        hadith_ids = get_hadith_ids()
        mask = np.array([hid in eval_ids for hid in hadith_ids])
        self.hadith_ids = hadith_ids[mask]
        self.embeddings = {
            "EN": get_english_embeddings()[mask],
            "AR": get_arabic_embeddings()[mask],
        }
        print(f"hadith_ids:       {hadith_ids.shape}")
        print(f"eval pool size:   {mask.sum()}")


def _bm25_candidates(res, query, language):
    from scripts import bm25

    scores = bm25(query, language, res.indexes[language], res.doc_lengths)
    ranked = sorted(scores, key=scores.get, reverse=True)
    return [doc_id for doc_id in ranked if doc_id in res.eval_ids]


def _bi_encoder(res, query, language):
    from scripts import semantic_reranker

    return semantic_reranker(
        query=query,
        language=language,
        candidate_ids=_bm25_candidates(res, query, language)[:BM25_CANDIDATES],
        model=res.model,
        embeddings=res.embeddings[language],
        hadith_ids=res.hadith_ids,
        top_k=BM25_CANDIDATES,
    )


def _rrf(res, query, language):
    from scripts import bm25_semantic_rrf

    return bm25_semantic_rrf(
        query=query,
        language=language,
        index=res.indexes[language],
        judged_ids=res.eval_ids,
        doc_lengths=res.doc_lengths,
        corpus_embeddings_normed=res.embeddings[language],
        hadith_ids=res.hadith_ids,
        model=res.model,
    )


def _cross_encoder(res, query, language):
    from scripts import cross_encoder_rerank

    candidates = _bm25_candidates(res, query, language)[:CROSS_ENCODER_CANDIDATES]
    texts = res.texts[language]
    return cross_encoder_rerank(
        query=query,
        language=language,
        candidate_ids=candidates,
        hadith_texts={hid: texts[hid] for hid in candidates if hid in texts},
    )


_SIMULATED = {"bi-encoder": _bi_encoder, "rrf": _rrf, "cross-encoder": _cross_encoder}


def simulated_pipeline(res, query, language, model_type):
    if model_type not in _SIMULATED:
        raise ValueError(f"Unknown model_type: {model_type}")
    return _SIMULATED[model_type](res, query, language)


def _lexical_systems(res):
    from scripts import (
        bm25,
        bm25_tfidf_hybrid,
        bm25_with_expansion,
        get_hadith,
        hybrid_with_expansion,
        ranked_term_overlap,
        tf_idf,
    )

    def idx(lang):
        return res.indexes[lang]

    return {
        "BM25": lambda q, lang: bm25(q, lang, idx(lang), res.doc_lengths),
        "TF_IDF": lambda q, lang: tf_idf(q, lang, idx(lang), res.doc_lengths),
        "Term Overlap": lambda q, lang: ranked_term_overlap(q, lang, idx(lang)),
        "BM25_ROCCHIO": lambda q, lang: bm25_with_expansion(
            q, lang, idx(lang), res.doc_lengths, get_hadith
        ),
        "BM25_TF_IDF": lambda q, lang: bm25_tfidf_hybrid(q, lang, idx(lang), res.doc_lengths),
        "BM25_TF_IDF_ROCCHIO": lambda q, lang: hybrid_with_expansion(
            q, lang, idx(lang), res.doc_lengths, get_hadith
        ),
    }


def _dense_systems(res):
    from scripts import semantic_search_e5

    return {
        "COSINE_SIMILARITY": lambda q, lang: semantic_search_e5(
            query=q,
            language=lang,
            model=res.model,
            corpus_embeddings_normed=res.embeddings[lang],
            hadith_ids=res.hadith_ids,
            top_k=COSINE_TOP_K,
        )
    }


def _hybrid_systems(res):
    from scripts import final_search_pipeline

    simulated = {
        "BM25_SEMANTIC_RERANK": "bi-encoder",
        "BM25_RRF": "rrf",
        "BM25_CROSS_ENCODER": "cross-encoder",
    }
    systems = {
        name: (lambda q, lang, kind=kind: simulated_pipeline(res, q, lang, kind))
        for name, kind in simulated.items()
    }
    systems["FINAL_PIPELINE"] = lambda q, lang: final_search_pipeline(
        query=q,
        language=lang,
        index=res.indexes[lang],
        doc_lengths=res.doc_lengths,
        embeddings=res.embeddings[lang],
        hadith_ids=res.hadith_ids,
        model=res.model,
        eval_ids=res.eval_ids,
        texts_dict=res.texts[lang],
    )
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

    systems = build_systems(EvalResources(eval_ids))
    all_results = evaluate_systems(systems, eval_inputs, eval_ids, k)

    graded_qids = {qid for qid, rel in zip(query_ids, relevant_list) if rel}
    filtered = filter_graded_queries(all_results, graded_qids)
    print(f"\n{'='*60}\nStatistical Analysis ({len(graded_qids)} graded queries)\n{'='*60}")
    stats_output = run_analysis(filtered, baseline="BM25", k=k)
    latex = generate_latex_table(filtered, stats_output["pairwise_tests"], baseline="BM25", k=k)

    save_outputs(names, all_results, stats_output, latex)
    print_best_systems(stats_output, filtered)
