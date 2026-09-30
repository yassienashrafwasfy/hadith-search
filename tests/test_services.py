from services import build_results, overall_summary, summarize_query
from services.agreement import QueryAgreement


def test_build_results_filters_and_orders(_db_session):
    out = build_results(_db_session, {1: 0.9, 2: 0.5, 3: 0.7}, None, "Bukhari")
    assert [r.hadith.hadith_id for r in out] == [1, 3]
    out = build_results(_db_session, {1: 0.9, 2: 0.5}, "Hasan", None)
    assert [r.hadith.hadith_id for r in out] == [2]


def test_build_results_keeps_the_ranking_order(_db_session):
    out = build_results(_db_session, {3: 0.9, 1: 0.5, 2: 0.1})
    assert [r.hadith.hadith_id for r in out] == [3, 1, 2]
    assert out[0].hadith.book == "Bukhari" and out[0].score == 0.9


def test_build_results_skips_unknown_ids(_db_session):
    assert build_results(_db_session, {99: 1.0}, None, None) == []


def test_build_results_top_k(_db_session):
    assert len(build_results(_db_session, {1: 3, 2: 2, 3: 1}, None, None, top_k=2)) == 2


def test_summarize_needs_two_annotators():
    r = summarize_query("q1", "text", [1, 2], {7: {1: 0, 2: 1}})
    assert r.entry["kappa"] is None and r.entry["annotators"] == 1


def test_summarize_needs_two_common_labels():
    r = summarize_query("q1", "t", [1, 2, 3], {1: {1: 1}, 2: {1: 1}})
    assert r.entry["common_labeled"] == 1 and r.entry["kappa"] is None


def test_summarize_perfect_agreement():
    labels = {1: {1: 0, 2: 1, 3: 2}, 2: {1: 0, 2: 1, 3: 2}}
    r = summarize_query("q1", "t", [1, 2, 3], labels)
    assert r.entry["kappa"] == 1.0
    assert r.entry["raw_agreement"] == 1.0
    assert r.entry["common_labeled"] == 3


def test_summarize_partial_agreement():
    labels = {1: {1: 0, 2: 1, 3: 2, 4: 0}, 2: {1: 0, 2: 1, 3: 0, 4: 2}}
    r = summarize_query("q1", "t", [1, 2, 3, 4], labels)
    assert r.entry["raw_agreement"] == 0.5
    assert r.entry["kappa"] < 1


def test_overall_summary():
    rs = [QueryAgreement({}, 1.0, 0.5), QueryAgreement({}, 0.5, None), QueryAgreement({})]
    assert overall_summary(rs, 5) == {
        "mean_kappa": 0.75,
        "mean_spearman": 0.5,
        "queries_with_agreement": 2,
        "total_queries": 5,
    }
    assert overall_summary([], 0)["mean_kappa"] is None


async def test_agreement_endpoint(_client, _auth_headers):
    res = await _client.get("/api/v1/agreement", headers=_auth_headers)
    assert res.status_code == 200
    assert res.json()["overall"]["total_queries"] == 3
