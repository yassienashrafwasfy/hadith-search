"""Public script surface: `from scripts import bm25, get_model, preprocess_english, ...`.

Names resolve lazily (PEP 562) so `import scripts` stays cheap: the retrieval, NLP and
model-loading modules are only imported when one of their names is first used.
Submodules (`from scripts import data_creation`) still import normally.
"""

from typing import TYPE_CHECKING

from lazy_exports import exports_by_module, install

_EXPORTS = {
    "scripts.search": [
        "bm25",
        "bm25_cross_encoder_rerank",
        "bm25_semantic_rrf",
        "bm25_tfidf_hybrid",
        "bm25_with_expansion",
        "build_id2idx",
        "cosine_similarity_search",
        "cross_encoder_rerank",
        "final_search_pipeline",
        "get_hadith",
        "get_ranked_ids",
        "hybrid_with_expansion",
        "query_expansion",
        "ranked_term_overlap",
        "rrf_fusion",
        "semantic_reranker",
        "semantic_search_e5",
        "tf_idf",
    ],
    "scripts.loading": [
        "get_arabic_embeddings",
        "get_arabic_inverted_index",
        "get_document_lengths",
        "get_english_embeddings",
        "get_english_inverted_index",
        "get_english_lemmatizer",
        "get_hadith_ids",
        "get_hadiths_df",
        "get_mle",
        "get_model",
    ],
    "scripts.preprocess": [
        "normalize_arabic_text",
        "preprocess_arabic",
        "preprocess_english",
    ],
    "scripts.evaluation": [
        "MAP",
        "average_precision",
        "dcg",
        "evaluate_query",
        "evaluate_query_at_k",
        "evaluate_system",
        "f1_score",
        "jaccard_similarity",
        "mean_reciprocal_rank",
        "normalized_dcg",
        "normalized_dcg_at_k",
        "precision",
        "precision_at_k",
        "recall",
        "recall_at_k",
        "reciprocal_rank",
    ],
}

install(globals(), exports_by_module(_EXPORTS))


if TYPE_CHECKING:  # static analysis / IDE completion only
    from scripts.evaluation import (  # noqa: F401
        MAP,
        average_precision,
        dcg,
        evaluate_query,
        evaluate_query_at_k,
        evaluate_system,
        f1_score,
        jaccard_similarity,
        mean_reciprocal_rank,
        normalized_dcg,
        normalized_dcg_at_k,
        precision,
        precision_at_k,
        recall,
        recall_at_k,
        reciprocal_rank,
    )
    from scripts.loading import (  # noqa: F401
        get_arabic_embeddings,
        get_arabic_inverted_index,
        get_document_lengths,
        get_english_embeddings,
        get_english_inverted_index,
        get_english_lemmatizer,
        get_hadith_ids,
        get_hadiths_df,
        get_mle,
        get_model,
    )
    from scripts.preprocess import (  # noqa: F401
        normalize_arabic_text,
        preprocess_arabic,
        preprocess_english,
    )
    from scripts.search import (  # noqa: F401
        bm25,
        bm25_cross_encoder_rerank,
        bm25_semantic_rrf,
        bm25_tfidf_hybrid,
        bm25_with_expansion,
        build_id2idx,
        cosine_similarity_search,
        cross_encoder_rerank,
        final_search_pipeline,
        get_hadith,
        get_ranked_ids,
        hybrid_with_expansion,
        query_expansion,
        ranked_term_overlap,
        rrf_fusion,
        semantic_reranker,
        semantic_search_e5,
        tf_idf,
    )
