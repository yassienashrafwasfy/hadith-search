import json

from pytest_bdd import given, parsers, scenarios, then, when

from features import Features
from main import create_app
from services.retrieval import enabled_systems

scenarios("../../docs/behaviours/search.feature")

SEARCH = "/api/v1/searches"

_BAD_PARAMS = {
    "q=x, method=bm25, lang=fr": {"q": "x", "method": "bm25", "lang": "fr"},
    "q empty, method=bm25": {"q": "", "method": "bm25"},
    "method=bm25 and no q": {"method": "bm25"},
    "q=x and no method": {"q": "x"},
    "q=x, method=nope": {"q": "x", "method": "nope"},
}


@given("the app runs with the search feature on")
def _search_is_on(_search_api):
    """`_search_client` mounts the real search router."""


@given("the corpus, the BM25 index and the embeddings are loaded")
def _index_is_loaded(_search_index):
    """The three-hadith corpus, its index and its vectors are in the test schema."""


def _search(api, **params):
    return api("GET", SEARCH, params=params)


@when(
    parsers.parse(
        'a client sends GET /api/v1/searches with q "{query}", method "{method}" and lang "{lang}"'
    )
)
def _search_with_lang(_search_api, query, method, lang):
    _search(_search_api, q=query, method=method, lang=lang)


@then("number_of_results equals the length of results")
def _count_matches(_ctx):
    body = _ctx["responses"][0].json()
    assert body["number_of_results"] == len(body["results"]) > 0


@then('the Server-Timing header starts with "search;dur="')
def _server_timing(_ctx):
    assert _ctx["responses"][0].headers["server-timing"].startswith("search;dur=")


@when(parsers.parse('a client searches "{query}" with method "{method}"'))
def _search_plain(_search_api, query, method):
    _search(_search_api, q=query, method=method)


@when(parsers.parse('a client searches "{query}" with method "{method}" and lang "{lang}"'))
def _search_lang(_search_api, query, method, lang):
    _search(_search_api, q=query, method=method, lang=lang)


@when(parsers.parse('a client searches "{query}" with method "{method}" and book_filter "{book}"'))
def _search_book(_search_api, query, method, book):
    _search(_search_api, q=query, method=method, book_filter=book)


@when(
    parsers.parse('a client searches "{query}" with method "{method}" and grade_filter "{grade}"')
)
def _search_grade(_search_api, query, method, grade):
    _search(_search_api, q=query, method=method, grade_filter=grade)


@then("each result has a hadith and a score")
def _results_have_hadith_and_score(_ctx):
    results = _ctx["responses"][0].json()["results"]
    assert results and all({"hadith", "score"} <= set(r) for r in results)


@then("the scores are in descending order")
def _scores_descend(_ctx):
    scores = [r["score"] for r in _ctx["responses"][0].json()["results"]]
    assert scores == sorted(scores, reverse=True)


@then("the body has no response_time_ms field, because timing is in the Server-Timing header")
def _no_timing_in_body(_ctx):
    assert "response_time_ms" not in _ctx["responses"][0].json()


@then("only hadiths from the Muslim book are returned")
def _only_muslim(_ctx):
    results = _ctx["responses"][0].json()["results"]
    assert results and {r["hadith"]["book"] for r in results} == {"Muslim"}


@then("number_of_results is 0")
def _no_results(_ctx):
    assert _ctx["responses"][0].json()["number_of_results"] == 0


@when(parsers.re(r'a client sends GET /api/v1/searches with (?P<params>[^"]+)'))
def _search_bad_params(_search_api, params):
    _search(_search_api, **_BAD_PARAMS[params])


@given("a client has fetched a search once")
def _fetch_once(_search_api, _ctx):
    _ctx["first"] = _search(_search_api, q="prayer", method="bm25")


@then('the response has "Cache-Control: public, max-age=300" and an ETag')
def _cache_headers(_ctx):
    first = _ctx["first"]
    assert first.headers["cache-control"] == "public, max-age=300" and first.headers["etag"]


@when("the client repeats it with If-None-Match set to that ETag")
def _repeat_with_etag(_search_api, _ctx):
    _search_api(
        "GET",
        SEARCH,
        params={"q": "prayer", "method": "bm25"},
        headers={"If-None-Match": _ctx["first"].headers["etag"]},
    )


@then("the status is 304 and the body is empty")
def _not_modified(_ctx):
    response = _ctx["responses"][0]
    assert response.status_code == 304 and response.content == b""


@then(parsers.parse('_links.self.href is "{expected}"'))
def _self_link(_ctx, expected):
    assert _ctx["responses"][0].json()["_links"]["self"]["href"] == expected


@then(parsers.parse('_links.methods.href is "{expected}"'))
def _methods_link(_ctx, expected):
    assert _ctx["responses"][0].json()["_links"]["methods"]["href"] == expected


@when("a client sends POST /api/v1/searches")
def _post_search(_search_api):
    _search_api("POST", SEARCH, json={"query": "x"})


@when("a client sends GET /api/v1/search-methods")
def _list_methods(_search_api):
    _search_api("GET", "/api/v1/search-methods")


@then("each method has a slug, its languages and a templated search link")
def _method_shape(_ctx):
    methods = _ctx["responses"][0].json()["methods"]
    for method in methods:
        assert method["slug"] and method["languages"]
        assert method["_links"]["search"]["templated"] is True


@then(parsers.parse('"{slug}" lists languages {languages}'))
def _method_languages(_ctx, slug, languages):
    by_slug = {m["slug"]: m for m in _ctx["responses"][0].json()["methods"]}
    assert by_slug[slug]["languages"] == json.loads(languages)


@given("dense_retrieval is off")
def _dense_off(_ctx):
    _ctx["features"] = Features(dense_retrieval=False)


@given("search is off")
def _search_off(_ctx):
    _ctx["features"] = Features(search=False)


@then("the methods offered are term-overlap, tfidf, bm25, bm25-tf-idf, bm25-prf and exact")
def _lexical_only(_ctx):
    slugs = {s.slug for s in enabled_systems(_ctx["features"])}
    assert slugs == {"term-overlap", "tfidf", "bm25", "bm25-tf-idf", "bm25-prf", "exact"}


@then("no method is offered and no /api/v1/search route is mounted")
def _nothing_mounted(_ctx):
    assert enabled_systems(_ctx["features"]) == []
    paths = {r.path for r in create_app(_ctx["features"], static_dir="").routes}
    assert not any(p.startswith("/api/v1/search") for p in paths)
