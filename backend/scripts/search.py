import os
import time

import requests
from camel_tools.utils.dediac import dediac_ar
from dotenv import load_dotenv

from scripts.preprocess import normalize_arabic_text

load_dotenv()
JINA_API_KEY = os.getenv("JINA_API_KEY")
JINA_RATE_LIMIT_SECONDS = 30
_last_jina_call = 0.0


def wait_for_jina_rate_limit():
    global _last_jina_call
    now = time.monotonic()
    elapsed = now - _last_jina_call
    if _last_jina_call and elapsed < JINA_RATE_LIMIT_SECONDS:
        time.sleep(JINA_RATE_LIMIT_SECONDS - elapsed)
    _last_jina_call = time.monotonic()


def rrf_fusion(ranked_lists: list[dict[int, int]], k: int = 60) -> dict[int, float]:
    """
    Each input dict is {hadith_id: rank}, where rank is 1-indexed.
    """
    scores: dict[int, float] = {}

    for ranked_list in ranked_lists:
        for hadith_id, rank in ranked_list.items():
            scores[hadith_id] = scores.get(hadith_id, 0.0) + 1.0 / (k + rank)

    return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))


def cross_encoder_rerank(
    query: str,
    language: str,
    candidate_ids: list[int],
    hadith_texts: dict[int, str],
    top_k: int = 100,
) -> dict[int, float]:
    valid_ids = [hid for hid in candidate_ids if hid in hadith_texts]
    if not valid_ids:
        return {}
    if not JINA_API_KEY:
        raise RuntimeError("JINA_API_KEY is not set; required for Jina cross-encoder reranking")
    query = normalize_arabic_text(dediac_ar(query)) if language == "AR" else query
    documents = [hadith_texts[hid] for hid in valid_ids]
    wait_for_jina_rate_limit()
    response = requests.post(
        "https://api.jina.ai/v1/rerank",
        headers={"Authorization": f"Bearer {JINA_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "jina-reranker-v3",
            "query": query,
            "documents": documents,
            "top_n": top_k,
            "return_documents": False,
        },
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Jina rerank failed with status {response.status_code}: {response.text[:500]}"
        )
    results = response.json()["results"]
    return {valid_ids[r["index"]]: float(r["relevance_score"]) for r in results}
