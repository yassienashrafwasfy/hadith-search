import pytest

SEARCH = "/api/v1/searches"


@pytest.mark.parametrize(
    "method",
    [
        "term-overlap",
        "tfidf",
        "bm25",
        "bm25-tf-idf",
        "bm25-prf",
        "semantic-rerank",
        "cosine-similarity",
        "semantic-rrf",
    ],
)
async def test_every_search_method_returns_results(_search_client, method):
    res = await _search_client.get(SEARCH, params={"q": "prayer", "method": method})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["number_of_results"] == len(body["results"]) > 0
    assert res.headers["server-timing"].startswith("search;dur=")


async def test_bm25_result_shape_and_order(_search_client):
    body = (await _search_client.get(SEARCH, params={"q": "prayer", "method": "bm25"})).json()
    results = body["results"]
    assert {r["hadith"]["hadith_id"] for r in results} == {1, 3}
    assert results[0]["hadith"]["book"] in {"Bukhari", "Muslim"}
    assert results[0]["score"] >= results[1]["score"]
    assert "response_time_ms" not in body  # timing is in the Server-Timing header


async def test_search_is_cacheable_with_etag(_search_client):
    params = {"q": "prayer", "method": "bm25"}
    first = await _search_client.get(SEARCH, params=params)
    assert first.headers["cache-control"] == "public, max-age=300"
    again = await _search_client.get(
        SEARCH, params=params, headers={"If-None-Match": first.headers["etag"]}
    )
    assert again.status_code == 304 and again.content == b""


async def test_search_links(_search_client):
    body = (
        await _search_client.get(
            SEARCH, params={"q": "fasting", "method": "bm25", "book_filter": "Muslim"}
        )
    ).json()
    assert body["_links"]["self"]["href"] == (
        "/api/v1/searches?q=fasting&method=bm25&lang=en&book_filter=Muslim"
    )
    assert body["_links"]["methods"]["href"] == "/api/v1/search-methods"


async def test_book_and_grade_filters(_search_client):
    only_muslim = (
        await _search_client.get(
            SEARCH, params={"q": "fasting", "method": "bm25", "book_filter": "Muslim"}
        )
    ).json()
    assert [r["hadith"]["hadith_id"] for r in only_muslim["results"]] == [2]
    none = (
        await _search_client.get(
            SEARCH, params={"q": "fasting", "method": "bm25", "grade_filter": "Da'if (Weak)"}
        )
    ).json()
    assert none["number_of_results"] == 0


@pytest.mark.parametrize(
    "params",
    [
        {"q": "x", "method": "bm25", "lang": "fr"},
        {"q": "", "method": "bm25"},
        {"method": "bm25"},
        {"q": "x"},
        {"q": "x", "method": "nope"},
    ],
)
async def test_bad_search_requests_are_422(_search_client, params):
    res = await _search_client.get(SEARCH, params=params)
    assert res.status_code == 422
    assert res.headers["content-type"] == "application/problem+json"


async def test_search_methods_lists_links(_search_client):
    body = (await _search_client.get("/api/v1/search-methods")).json()
    slugs = [m["slug"] for m in body["methods"]]
    assert "bm25" in slugs and "semantic-rrf" in slugs
    template = body["methods"][0]["_links"]["search"]
    assert template["templated"] is True and "{q}" in template["href"]


async def test_post_to_searches_is_405(_search_client):
    res = await _search_client.post(SEARCH, json={"query": "x"})
    assert res.status_code == 405
    assert res.headers["content-type"] == "application/problem+json"


async def test_hybrid_results_come_back_ranked(_search_client):
    body = (
        await _search_client.get(SEARCH, params={"q": "prayer fasting", "method": "bm25-tf-idf"})
    ).json()
    scores = [r["score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)
