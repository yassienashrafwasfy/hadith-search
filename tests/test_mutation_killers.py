"""Assertions that pin exact values/shapes (found by mutmut survivors)."""

import types

import numpy as np
import pytest

import lazy_exports
from features import load_features
from services import agreement, results


def _row(**values):
    """A result row as the query returns it: every selected column, None when unset."""
    names = results._COLUMNS
    columns = dict.fromkeys((getattr(c, "key", None) or c.name for c in names))
    return types.SimpleNamespace(**(columns | values))


def test_to_hadith_maps_every_column():
    row = _row(
        id=5,
        Book="B",
        English_Text="en",
        Arabic_Text="ar",
        Chapter_Title_English="cen",
        Chapter_Title_Arabic="car",
        Normalized_Grade="Sahih",
        English_Grade="raw",
    )
    assert results._to_hadith(row).model_dump() == {
        "hadith_id": 5,
        "book": "B",
        "hadith_en_text": "en",
        "hadith_ar_text": "ar",
        "chapter_title_en": "cen",
        "chapter_title_ar": "car",
        "grade": "Sahih",
        "raw_grade": "raw",
        "reference": "",
        "in_book_reference": "",
    }


def test_to_hadith_defaults():
    h = results._to_hadith(_row(id=1))
    assert (h.book, h.hadith_en_text, h.hadith_ar_text) == ("", "", "")
    assert (h.chapter_title_en, h.chapter_title_ar) == ("", "")
    assert (h.grade, h.raw_grade) == ("Unknown", "Unknown")
    assert (h.reference, h.in_book_reference) == ("", "")


def test_build_results_skips_missing_but_keeps_going(_db_session):
    out = results.build_results(_db_session, {99: 1.0, 1: 0.5, 2: 0.4})
    assert [r.hadith.hadith_id for r in out] == [1, 2]
    assert [r.score for r in out] == [0.5, 0.4]


def test_build_results_filters_skip_only_the_failing_row(_db_session):
    out = results.build_results(_db_session, {2: 1.0, 1: 0.5, 3: 0.4}, book_filter="Bukhari")
    assert [r.hadith.hadith_id for r in out] == [1, 3]
    out = results.build_results(_db_session, {2: 1.0, 1: 0.5}, "Hasan", "Muslim")
    assert [r.hadith.hadith_id for r in out] == [2]
    assert results.build_results(_db_session, {2: 1.0}, "Hasan", "Bukhari") == []


def test_build_results_default_top_k():
    assert results.DEFAULT_TOP_K == 500


def test_finite_drops_none_and_nan():
    assert agreement._finite([1, None, float("nan"), 2.5]) == [1.0, 2.5]


def test_raw_agreement_is_per_document():
    matrix = np.array([[0, 1, 2, 0], [0, 1, 0, 2], [0, 1, 2, 2]])
    assert agreement._raw_agreement(matrix) == 0.5
    assert agreement._raw_agreement(np.array([[1, 2], [1, 3]])) == 0.5


def test_pairwise_means_values():
    matrix = np.array([[0, 1, 2, 0, 1], [0, 1, 2, 0, 1], [2, 1, 0, 2, 1]])
    kappa, rho = agreement._pairwise_means(matrix)
    assert rho == pytest.approx((1.0 + -1.0 + -1.0) / 3, abs=0.4)
    assert kappa < 1


def test_summarize_query_entry_keys_and_types():
    labels = {1: {1: 0, 2: 1, 3: 2, 4: 0}, 2: {1: 0, 2: 1, 3: 2, 4: 1}}
    r = agreement.summarize_query("q", "text", [1, 2, 3, 4, 9], labels)
    assert set(r.entry) == {
        "query_id",
        "query",
        "annotators",
        "common_labeled",
        "kappa",
        "spearman",
        "raw_agreement",
    }
    assert r.entry["query_id"] == "q" and r.entry["query"] == "text"
    assert r.entry["annotators"] == 2 and r.entry["common_labeled"] == 4
    assert r.entry["raw_agreement"] == 0.75
    assert r.kappa is not None and r.spearman is not None
    assert r.entry["kappa"] == round(r.kappa, 4)


def test_summarize_query_too_few_returns_base_keys():
    one = agreement.summarize_query("q", "t", [1], {1: {1: 0}})
    assert one.entry == {
        "query_id": "q",
        "query": "t",
        "annotators": 1,
        "kappa": None,
        "agreement": None,
    }
    few = agreement.summarize_query("q", "t", [1, 2], {1: {1: 0}, 2: {1: 0}})
    assert few.entry["common_labeled"] == 1 and few.kappa is None


def test_summarize_query_needs_pooled_docs_labelled_by_all():
    labels = {1: {1: 0, 2: 1, 5: 2}, 2: {1: 0, 2: 1}}
    r = agreement.summarize_query("q", "t", [1, 2, 5], labels)
    assert r.entry["common_labeled"] == 2
    assert agreement.MIN_COMMON_LABELS == 2 and agreement.MIN_ANNOTATORS == 2


def test_overall_summary_rounding_keys():
    out = agreement.overall_summary([agreement.QueryAgreement({}, 0.123456, 0.987654)], 3)
    assert out == {
        "mean_kappa": 0.1235,
        "mean_spearman": 0.9877,
        "queries_with_agreement": 1,
        "total_queries": 3,
    }
    assert agreement._mean([]) is None and agreement._round(None) is None


def test_features_error_message_lists_valid_values():
    with pytest.raises(ValueError) as e:
        load_features({"FEATURE_SEARCH": "maybe"})
    msg = str(e.value)
    assert "'maybe'" in msg and "'on'" in msg and "'off'" in msg


def test_features_defaults():
    f = load_features({})
    assert (f.annotation, f.kv_pairs, f.benchmark, f.search, f.dense_retrieval) == (True,) * 5
    assert f.eager_model is False
    assert load_features({"APP_MODE": "bogus"}).search is True


def test_load_features_reads_os_environ(monkeypatch):
    monkeypatch.setenv("FEATURE_KV_PAIRS", "false")
    assert load_features().kv_pairs is False


def test_lazy_exports_install_and_getattr():
    ns = {"__name__": "pkg"}
    symbols = lazy_exports.exports_by_module({"os.path": ["join", "basename"]})
    assert symbols == {"join": ("os.path", "join"), "basename": ("os.path", "basename")}
    lazy_exports.install(ns, symbols)
    assert ns["__all__"] == ["basename", "join"]
    assert ns["__getattr__"]("join")("a", "b") == "a/b"
    assert "join" in ns  # cached
    with pytest.raises(AttributeError, match="module 'pkg' has no attribute 'nope'"):
        ns["__getattr__"]("nope")
    assert isinstance(ns["__getattr__"], types.FunctionType)


def test_summarize_needs_more_than_min_common():
    labels = {1: {1: 0, 2: 1}, 2: {1: 0, 2: 1}}
    r = agreement.summarize_query("q", "t", [1, 2], labels)  # exactly MIN_COMMON_LABELS
    assert r.entry["common_labeled"] == 2 and "spearman" in r.entry
    sparse = agreement.summarize_query("q", "t", [1], {1: {1: 0}, 2: {1: 0}})
    assert sparse.entry == {
        "query_id": "q",
        "query": "t",
        "annotators": 2,
        "common_labeled": 1,
        "kappa": None,
        "agreement": None,
    }


def test_spearman_is_rho_not_p():
    labels = {1: {1: 0, 2: 1, 3: 2, 4: 0}, 2: {1: 0, 2: 1, 3: 2, 4: 0}}
    r = agreement.summarize_query("q", "t", [1, 2, 3, 4], labels)
    assert r.spearman == pytest.approx(1.0) and r.entry["spearman"] == 1.0


def test_raw_agreement_uses_first_annotator_row():
    assert agreement._raw_agreement(np.array([[1, 2], [3, 2], [3, 2]])) == 0.5


def test_lazy_install_caches_resolved_value():
    ns = {"__name__": "pkg"}
    lazy_exports.install(ns, {"join": ("os.path", "join")})
    ns["__getattr__"]("join")
    import os.path

    assert ns["join"] is os.path.join


def test_app_mode_default_and_explicit():
    assert load_features({}).eager_model is False
    assert load_features({"APP_MODE": "research"}).eager_model is True
    assert load_features({"APP_MODE": "annotation"}).search is False


class TestRetrievalService:
    def test_registry_slugs_and_requirements(self):
        from services.retrieval import SYSTEMS

        assert {s: SYSTEMS[s].requires for s in SYSTEMS} == {
            "term-overlap": (),
            "tfidf": (),
            "bm25": (),
            "bm25-tf-idf": (),
            "bm25-prf": (),
            "semantic-rerank": ("dense_retrieval",),
            "cosine-similarity": ("dense_retrieval",),
            "semantic-rrf": ("dense_retrieval",),
            "exact": (),
            "exact-semantic-rrf": ("dense_retrieval",),
        }
        assert all(SYSTEMS[s].slug == s for s in SYSTEMS)

    def test_run_search_reports_counts(self, _db_session):
        from models import SearchRequest
        from services.retrieval import RetrievalSystem, SearchContext, run_search

        system = RetrievalSystem("x", lambda ctx, q, lang: {1: 2.0, 3: 1.0} if lang == "EN" else {})
        ctx = SearchContext(session=_db_session, model=lambda: None)
        res = run_search(system, ctx, SearchRequest(query="q", lang="en"))
        assert res.number_of_results == 2
        assert [r.score for r in res.results] == [2.0, 1.0]


def test_build_results_filters_before_the_cut(_db_session):
    # Hadith 1 and 3 are Bukhari, 2 is Muslim. With top_k=1 the Muslim one must still be found
    # when it is the only one left after the book filter, even though it ranks last.
    raw = {1: 0.9, 3: 0.8, 2: 0.1}
    out = results.build_results(_db_session, raw, book_filter="Muslim", top_k=1)
    assert [r.hadith.hadith_id for r in out] == [2]
    assert [r.hadith.hadith_id for r in results.build_results(_db_session, raw, top_k=2)] == [1, 3]
