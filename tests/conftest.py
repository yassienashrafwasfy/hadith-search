"""Shared fixtures.

Real artifacts (hadiths.db, .pkl indices, .npy _embeddings, E5 model, Jina API)
are not available in CI/dev checkouts, so everything here is a tiny in-memory
stand-in. Query preprocessing (NLTK / CAMeL) is mocked to a whitespace split
in `_mock_preprocess`.
"""

import json

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert

from models import Hadith

# ---------- tiny corpus ----------

_HADITHS = {
    1: dict(
        Book="Bukhari",
        English_Text="prayer is the pillar of faith",
        Arabic_Text="الصلاة عماد الدين",
        Normalized_Grade="Sahih",
        Chapter_Title_English="Prayer",
        Chapter_Title_Arabic="الصلاة",
        Grade="Sahih",
        Reference="1",
        **{"In-book reference": "1:1"},
        Hadith_Number=1,
        Chapter_Number=1,
        English_Matn="prayer is the pillar of faith",
        Arabic_Matn="الصلاة عماد الدين",
        Preprocessed_English_Matn="prayer pillar faith",
        Preprocessed_Arabic_Matn="صلاه عماد دين",
    ),
    2: dict(
        Book="Muslim",
        English_Text="fasting is a shield",
        Arabic_Text="الصيام جنة",
        Normalized_Grade="Hasan",
        Chapter_Title_English="Fasting",
        Chapter_Title_Arabic="الصيام",
        Grade="Hasan",
        Reference="2",
        **{"In-book reference": "2:1"},
        Hadith_Number=2,
        Chapter_Number=2,
        English_Matn="fasting is a shield",
        Arabic_Matn="الصيام جنة",
        Preprocessed_English_Matn="fasting shield",
        Preprocessed_Arabic_Matn="صيام جنه",
    ),
    3: dict(
        Book="Bukhari",
        English_Text="prayer and fasting bring reward",
        Arabic_Text="الصلاة والصيام أجر",
        Normalized_Grade="Sahih",
        Chapter_Title_English="Reward",
        Chapter_Title_Arabic="الأجر",
        Grade="Sahih",
        Reference="3",
        **{"In-book reference": "3:1"},
        Hadith_Number=3,
        Chapter_Number=3,
        English_Matn="prayer and fasting bring reward",
        Arabic_Matn="الصلاة والصيام أجر",
        Preprocessed_English_Matn="prayer fasting bring reward",
        Preprocessed_Arabic_Matn="صلاه صيام اجر",
    ),
}


TEST_SECRET = "test-secret-" * 4  # 44 characters, above the HS256 minimum


@pytest.fixture
def _hadiths_df() -> pd.DataFrame:
    return pd.DataFrame.from_dict(_HADITHS, orient="index").rename_axis("id")


@pytest.fixture
def _inverted_index() -> dict:
    """term -> [(hadith_id, term_frequency)]"""
    return {
        "prayer": [(1, 1), (3, 1)],
        "pillar": [(1, 1)],
        "faith": [(1, 1)],
        "fasting": [(2, 1), (3, 1)],
        "shield": [(2, 1)],
        "reward": [(3, 1)],
    }


@pytest.fixture
def _document_lengths() -> dict:
    """hadith_id -> (arabic_len, english_len); index 0 is AR, 1 is EN (see search.bm25)."""
    docs = {1: (3, 3), 2: (2, 2), 3: (3, 4)}
    # filler docs so BM25/TF-IDF idf stays positive (a 3-doc corpus gives negative idf)
    docs.update({i: (3, 3) for i in range(100, 110)})
    return docs


@pytest.fixture
def _hadith_ids() -> np.ndarray:
    return np.array([1, 2, 3])


@pytest.fixture
def _embeddings() -> np.ndarray:
    """L2-normalised 3-d vectors: doc1 ~ x, doc2 ~ y, doc3 ~ between."""
    raw = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.7, 0.7, 0.0]])
    return raw / np.linalg.norm(raw, axis=1, keepdims=True)


class _FakeModel:
    """Stands in for SentenceTransformer; always encodes to the doc1 direction."""

    def encode(self, texts):
        return np.array([[1.0, 0.1, 0.0] for _ in texts])


@pytest.fixture
def _fake_model() -> _FakeModel:
    return _FakeModel()


@pytest.fixture
def _mock_preprocess(monkeypatch):
    """Replace NLTK/CAMeL preprocessing in scripts.search with lowercase whitespace split."""
    import scripts.search as s

    def fake(text):
        return " ".join(text.lower().split())

    monkeypatch.setattr(s, "preprocess_english", fake)
    monkeypatch.setattr(s, "preprocess_arabic", fake)
    return fake


@pytest.fixture
def _jina_env(monkeypatch):
    """Set a fake Jina key and disable the 30s rate-limit sleep."""
    import scripts.search as s

    monkeypatch.setattr(s, "JINA_API_KEY", "test-key")
    monkeypatch.setattr(s, "wait_for_jina_rate_limit", lambda: None)


# ---------- evaluation data ----------


@pytest.fixture
def _graded_relevant() -> dict[int, int]:
    return {1: 2, 2: 1, 3: 0}


# ---------- API / SQLite ----------


@pytest.fixture
def _data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "queries.json").write_text(json.dumps({"q1": "prayer", "q2": "fasting", "q3": "zakat"}))
    (d / "qrels_ungraded.json").write_text(json.dumps({"q1": [1, 3], "q2": [2, 3], "q3": [1]}))
    return d


@pytest.fixture
def _db_path(_data_dir):
    """Per-test SQLite file (tmp_path), so tests are isolated and safe under pytest-xdist."""
    return _data_dir / "hadiths.db"


@pytest_asyncio.fixture
async def _patched_paths(monkeypatch, _data_dir, _db_path, _hadiths_df):
    """Point every module-level path constant at the temp data dir and create tables."""
    import database
    import routers.annotation as annotation
    import routers.auth as auth
    import routers.benchmark as benchmark

    monkeypatch.setattr(database, "DB_PATH", str(_db_path))
    for mod in (annotation, auth, benchmark):
        monkeypatch.setattr(mod, "QUERIES_PATH", str(_data_dir / "queries.json"))
    monkeypatch.setattr(annotation, "QRELS_UNGRADED_PATH", str(_data_dir / "qrels_ungraded.json"))
    for name in ("QRELS_RESULTS_PATH", "STATS_RESULTS_PATH", "COMPARISON_RESULTS_PATH"):
        monkeypatch.setattr(benchmark, name, str(_data_dir / (name.lower() + ".json")))
    monkeypatch.setattr(
        benchmark, "FINETUNED_RESULTS_TEMPLATE", str(_data_dir / "finetuned_results_{mode}.json")
    )
    monkeypatch.setattr(
        benchmark, "FINETUNED_STATS_TEMPLATE", str(_data_dir / "finetuned_stats_{mode}.json")
    )
    database.init_hadiths_table()
    columns = {c.name for c in Hadith.__table__.c}
    rows = [
        {k: v for k, v in row.items() if k in columns}
        for row in _hadiths_df.reset_index().to_dict("records")
    ]
    with database.get_sync_session() as session:
        session.execute(insert(Hadith), rows)
        session.commit()
    await database.init_annotation_tables()
    await database.init_kv_pairs_table()
    return _data_dir


@pytest_asyncio.fixture
async def _client(_patched_paths):
    """App with the annotation/auth/kv/benchmark routers only (no model or index loading)."""
    from ratelimit import install_rate_limiting
    from rest import install_error_handlers
    from routers import annotation, auth, benchmark, hadiths, kv_pairs
    from tokens import AuthSettings, auth_settings

    app = FastAPI()
    install_error_handlers(app)
    install_rate_limiting(app)
    for r in (annotation.router, auth.router, kv_pairs.router, benchmark.router, hadiths.router):
        app.include_router(r)
    app.dependency_overrides[auth_settings] = lambda: AuthSettings(TEST_SECRET, ttl_seconds=3600)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def _auth_headers(_client):
    """Sign up a fresh annotator; returns an Authorization header (auto-assigned q1, q2)."""
    res = await _client.post(
        "/api/v1/annotators", json={"username": "alice", "password": "secret123"}
    )
    assert res.status_code == 201
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest_asyncio.fixture
async def _kv_rows(_patched_paths):
    import database
    from models import KvPair

    async with database.get_session() as session:
        for i, (topic, status) in enumerate(
            [("prayer", "pending"), ("fasting", "pending"), ("prayer", "verified")], 1
        ):
            session.add(
                KvPair(
                    topic=topic,
                    language="en",
                    concept_en="c",
                    concept_ar="ك",
                    entity_en="e",
                    entity_ar="ع",
                    hadith_id=i,
                    status=status,
                    created_at=database.now_iso(),
                )
            )
        await session.commit()


@pytest_asyncio.fixture
async def _search_client(
    _patched_paths,
    _hadiths_df,
    _inverted_index,
    _document_lengths,
    _hadith_ids,
    _embeddings,
    _fake_model,
    _mock_preprocess,
    _jina_env,
    monkeypatch,
):
    """App with every search endpoint; all data/model dependencies injected as fakes."""
    from features import Features
    from routers.search import get_search_context, make_search_router
    from services.retrieval import SearchContext

    ctx = SearchContext(
        inverted_index=lambda lang: _inverted_index,
        embeddings=lambda lang: _embeddings,
        doc_lengths=lambda: _document_lengths,
        hadith_ids=lambda: _hadith_ids,
        model=lambda: _fake_model,
        hadiths_df=lambda: _hadiths_df,
        get_hadith=lambda hid: _hadiths_df.loc[hid].to_dict(),
    )
    from rest import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        make_search_router(Features(search=True, dense_retrieval=True, cross_encoder=True))
    )
    app.dependency_overrides[get_search_context] = lambda: ctx
    # Jina API replaced: score by document order, no network
    import scripts.search as s

    class _FakeResp:
        status_code = 200
        text = ""

        def __init__(self, n):
            self._n = n

        def json(self):
            return {
                "results": [{"index": i, "relevance_score": 1.0 - i * 0.1} for i in range(self._n)]
            }

    monkeypatch.setattr(
        s.requests, "post", lambda url, headers, json: _FakeResp(len(json["documents"]))
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _rate_limiter_off():
    """Off by default (tests make many calls from one address); tests that need it turn it on."""
    from ratelimit import limiter

    limiter.enabled = False
    limiter.reset()
    yield
    limiter.enabled = True
    limiter.reset()
