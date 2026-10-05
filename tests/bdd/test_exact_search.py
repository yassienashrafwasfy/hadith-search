from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../../docs/behaviours/exact-search.feature")

SEARCH = "/api/v1/searches"
SUGGESTIONS = "/api/v1/suggestions"

_BAD_PARAMS = {
    "no q": {},
    "q empty": {"q": ""},
    "q 101 letters": {"q": "a" * 101},
    "q=a, limit=0": {"q": "a", "limit": 0},
    "q=a, limit=11": {"q": "a", "limit": 11},
}


@given("the app runs with the search feature on")
def _search_is_on(_search_api):
    """`_search_client` mounts the real search and suggestions routers."""


@given("the corpus, the BM25 index and the embeddings are loaded")
def _index_is_loaded(_search_index):
    """The three-hadith corpus, its index, its exact text and its vectors are in the test schema."""


@when(parsers.parse('a client searches "{query}" with method "{method}"'))
def _search_plain(_search_api, query, method):
    _search_api("GET", SEARCH, params={"q": query, "method": method})


@when(parsers.parse('a client searches "{query}" with method "{method}" and lang "{lang}"'))
def _search_lang(_search_api, query, method, lang):
    _search_api("GET", SEARCH, params={"q": query, "method": method, "lang": lang})


@when(parsers.parse('a client sends GET /api/v1/suggestions with q "{query}"'))
def _suggest(_search_api, query):
    _search_api("GET", SUGGESTIONS, params={"q": query})


@when(parsers.re(r"a client sends GET /api/v1/suggestions with (?P<params>(?!q \").+)"))
def _suggest_bad(_search_api, params):
    _search_api("GET", SUGGESTIONS, params=_BAD_PARAMS[params])


def _body(ctx):
    return ctx["responses"][0].json()


@then(parsers.parse("the hadith ids returned are {ids}"))
def _ids_are(_ctx, ids):
    returned = [result["hadith"]["hadith_id"] for result in _body(_ctx)["results"]]
    assert sorted(returned) == [int(value) for value in ids.split(",")]


@then(parsers.parse("the first hadith id is {hadith_id:d}"))
def _first_id(_ctx, hadith_id):
    assert _body(_ctx)["results"][0]["hadith"]["hadith_id"] == hadith_id


@then("number_of_results is 0")
def _no_results(_ctx):
    assert _body(_ctx)["number_of_results"] == 0


@then(parsers.parse('did_you_mean is "{hint}"'))
def _hint_is(_ctx, hint):
    assert _body(_ctx)["did_you_mean"] == hint


@then("the body has no did_you_mean field")
def _no_hint(_ctx):
    assert "did_you_mean" not in _body(_ctx)


@then(parsers.parse('the first suggestion is the term "{text}"'))
def _first_suggestion(_ctx, text):
    assert _body(_ctx)["suggestions"][0] == {"text": text, "kind": "term", "lang": "en"}


@then(parsers.parse('_links.self.href is "{expected}"'))
def _self_link(_ctx, expected):
    assert _body(_ctx)["_links"]["self"]["href"] == expected
