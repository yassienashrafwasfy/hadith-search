import pytest

BODY = {"query": "prayer", "lang": "en"}


@pytest.mark.parametrize(
    "endpoint",
    [
        "term-overlap",
        "tfidf",
        "bm25",
        "bm25-tf-idf",
        "bm25-prf",
        "semantic-rerank",
        "cosine-similarity",
        "semantic-rrf",
        "final-pipeline",
        "cross-encoder-rerank",
    ],
)
async def test_every_search_endpoint_returns_results(_search_client, endpoint):
    res = await _search_client.post(f"/search/{endpoint}", json=BODY)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["number_of_results"] == len(body["results"]) > 0
    assert body["response_time_ms"] is not None


async def test_bm25_result_shape_and_order(_search_client):
    results = (await _search_client.post("/search/bm25", json=BODY)).json()["results"]
    assert {r["hadith"]["hadith_id"] for r in results} == {1, 3}
    assert results[0]["hadith"]["book"] in {"Bukhari", "Muslim"}
    assert results[0]["score"] >= results[1]["score"]


async def test_book_and_grade_filters(_search_client):
    only_muslim = (
        await _search_client.post(
            "/search/bm25", json={"query": "fasting", "book_filter": "Muslim"}
        )
    ).json()
    assert [r["hadith"]["hadith_id"] for r in only_muslim["results"]] == [2]
    none = (
        await _search_client.post(
            "/search/bm25", json={"query": "fasting", "grade_filter": "Da'if (Weak)"}
        )
    ).json()
    assert none["number_of_results"] == 0


async def test_invalid_lang_rejected(_search_client):
    assert (
        await _search_client.post("/search/bm25", json={"query": "x", "lang": "fr"})
    ).status_code == 422
