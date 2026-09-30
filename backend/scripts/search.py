def rrf_fusion(ranked_lists: list[dict[int, int]], k: int = 60) -> dict[int, float]:
    """
    Each input dict is {hadith_id: rank}, where rank is 1-indexed.
    """
    scores: dict[int, float] = {}

    for ranked_list in ranked_lists:
        for hadith_id, rank in ranked_list.items():
            scores[hadith_id] = scores.get(hadith_id, 0.0) + 1.0 / (k + rank)

    return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))
