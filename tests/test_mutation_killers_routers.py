"""Exact-value tests for the annotation, kv-pairs, benchmark and database layers.

They pin response keys, 404 texts, per-annotator and per-query scoping, and the flat hadith view
(each one was a surviving mutmut mutant).
"""

import types

import pandas as pd
import pytest

import database
from models import Annotation
from routers import annotation, benchmark

API = "/api/v1"


async def _sign_up(client, name):
    res = await client.post(f"{API}/annotators", json={"username": name, "password": "secret123"})
    assert res.status_code == 201
    return res.json()["annotator"]["id"], {"Authorization": f"Bearer {res.json()['access_token']}"}


# ---- annotation helpers --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("index", "total", "expected"),
    [(0, 5, 0), (3, 5, 3), (4, 5, 4), (5, 5, 4), (9, 5, 4), (0, 0, 0), (3, 0, 0), (-1, 5, -1)],
)
def test_clamp_index(index, total, expected):
    assert annotation._clamp_index(index, total) == expected


def test_hadith_text_entry_has_every_key_and_blanks_missing_values():
    row = types.SimpleNamespace(
        Arabic_Text="ع",
        English_Text="e",
        Book="B",
        Normalized_Grade="Sahih",
        Hadith_Number=7,
        Chapter_Number=3,
        Chapter_Title_English="CE",
        Chapter_Title_Arabic="CA",
    )
    assert annotation._hadith_text_entry(row) == {
        "arabic_hadith": "ع",
        "english_hadith": "e",
        "book": "B",
        "normalized_grade": "Sahih",
        "reference": "7",
        "in_book_reference": "3",
        "chapter_title_english": "CE",
        "chapter_title_arabic": "CA",
    }
    empty = types.SimpleNamespace(**dict.fromkeys(vars(row)))
    assert set(annotation._hadith_text_entry(empty).values()) == {""}


async def test_get_hadith_texts_joins_the_chapter_and_blanks_unknown_ids(_patched_paths):
    texts = await annotation.get_hadith_texts([2, 999])
    assert texts[2] == {
        "arabic_hadith": "الصيام جنة",
        "english_hadith": "fasting is a shield",
        "book": "Muslim",
        "normalized_grade": "Hasan",
        "reference": "2",
        "in_book_reference": "2",
        "chapter_title_english": "Fasting",
        "chapter_title_arabic": "الصيام",
    }
    assert texts[999] == {
        "arabic_hadith": "",
        "english_hadith": "",
        "book": "",
        "normalized_grade": "",
        "reference": "",
        "in_book_reference": "",
    }


def test_assignment_links_are_exact():
    assert annotation._assignment_links("q1") == {
        "self": {"href": f"{API}/assignments/q1"},
        "collection": {"href": f"{API}/assignments"},
        "progress": {"href": f"{API}/assignments/q1/progress"},
        "label": {"href": f"{API}/assignments/q1/labels/{{hadith_id}}", "templated": True},
    }


async def test_labels_and_progress_are_scoped_to_annotator_and_query(_client, _auth_headers):
    alice_id, alice = 1, _auth_headers
    bob_id, bob = await _sign_up(_client, "bobby")
    puts = [
        (alice, "q1", 1, 0),
        (bob, "q1", 1, 2),
        (bob, "q1", 3, 1),
        (alice, "q2", 3, 1),
    ]
    for headers, query_id, hadith_id, label in puts:
        res = await _client.put(
            f"{API}/assignments/{query_id}/labels/{hadith_id}",
            json={"label": label},
            headers=headers,
        )
        assert res.status_code == 201
        assert res.json()["_links"] == {
            "self": {"href": f"{API}/assignments/{query_id}/labels/{hadith_id}"},
            "assignment": {"href": f"{API}/assignments/{query_id}"},
        }
    await _client.put(f"{API}/assignments/q1/progress", json={"index": 1}, headers=bob)

    async with database.get_session() as session:
        assert await annotation.get_annotator_labels(alice_id, "q1", session) == {"1": 0}
        assert await annotation.get_annotator_labels(bob_id, "q1", session) == {"1": 2, "3": 1}
        assert await annotation.get_annotator_labels(alice_id, "q2", session) == {"3": 1}
        assert await annotation.get_progress(alice_id, "q1", session) == 0
        assert await annotation.get_progress(bob_id, "q1", session) == 1
        assert await annotation.get_progress(bob_id, "q2", session) == 0
        assert await annotation.verify_assignment(alice_id, "q1", session)
        assert not await annotation.verify_assignment(alice_id, "q9", session)
        assert not await annotation.verify_assignment(999, "q1", session)
        assert await annotation._labels_by_annotator(session, "q1") == {
            alice_id: {1: 0},
            bob_id: {1: 2, 3: 1},
        }
        assert await annotation._labels_by_annotator(session, "q2") == {alice_id: {3: 1}}
    listing = (await _client.get(f"{API}/assignments", headers=alice)).json()
    assert [(a["query_id"], a["graded"], a["current_index"]) for a in listing["assignments"]] == [
        ("q1", 1, 0),
        ("q2", 1, 0),
    ]
    assert listing["_links"] == {"self": {"href": f"{API}/assignments"}}
    detail = (await _client.get(f"{API}/assignments/q1", headers=bob)).json()
    assert detail["labels"] == {"1": 2, "3": 1} and detail["current_index"] == 1


async def test_unassigned_query_404_names_the_problem(_client, _auth_headers):
    res = await _client.get(f"{API}/assignments/q3", headers=_auth_headers)
    assert res.status_code == 404 and res.json()["detail"] == "No such assignment"


async def test_a_stored_progress_past_the_pool_is_shown_as_the_last_item(_client, _auth_headers):
    async with database.get_session() as session:
        await annotation.set_progress(1, "q1", 7, session)
        await session.commit()
    detail = (await _client.get(f"{API}/assignments/q1", headers=_auth_headers)).json()
    assert detail["current_index"] == 1
    assert [h["hadith_id"] for h in detail["pooled_hadiths"]] == [1, 3]
    assert detail["pooled_hadiths"][1]["chapter_title_english"] == "Reward"


async def test_agreement_uses_only_the_labels_of_the_same_query(_client, _auth_headers):
    _, bob = await _sign_up(_client, "bobby")
    for headers in (_auth_headers, bob):
        await _client.put(f"{API}/assignments/q1/labels/1", json={"label": 2}, headers=headers)
        await _client.put(f"{API}/assignments/q1/labels/3", json={"label": 0}, headers=headers)
    body = (await _client.get(f"{API}/agreement", headers=_auth_headers)).json()
    by_query = {entry["query_id"]: entry for entry in body["per_query"]}
    assert set(by_query) == {"q1", "q2", "q3"}
    assert by_query["q1"]["annotators"] == 2
    assert by_query["q2"]["annotators"] == 1


def test_load_json_returns_an_empty_dict_for_a_missing_file(tmp_path):
    assert annotation.load_json(str(tmp_path / "none.json")) == {}


def test_load_json_reads_utf8_whatever_the_locale(tmp_path, monkeypatch):
    path = tmp_path / "a.json"
    path.write_bytes('{"q": "صلاة"}'.encode())
    monkeypatch.setattr("locale.getencoding", lambda: "ascii")
    assert annotation.load_json(str(path)) == {"q": "صلاة"}
    assert benchmark.load_json(str(path)) == {"q": "صلاة"}


# ---- kv pairs and benchmark ----------------------------------------------------------------


async def test_kv_pair_shape_and_joined_hadith_text(_client, _kv_rows, _auth_headers):
    body = (await _client.get(f"{API}/kv-pairs?status=verified", headers=_auth_headers)).json()
    (pair,) = body["pairs"]
    assert {k: v for k, v in pair.items() if k != "created_at"} == {
        "id": 3,
        "topic": "prayer",
        "language": "en",
        "concept_en": "c",
        "concept_ar": "ك",
        "entity_en": "e",
        "entity_ar": "ع",
        "hadith_id": 3,
        "hadith_en": "prayer and fasting bring reward",
        "hadith_ar": "الصلاة والصيام أجر",
        "status": "verified",
        "verified_at": None,
        "_links": {"self": {"href": f"{API}/kv-pairs/3"}},
    }
    assert pair["created_at"]


async def test_benchmark_files_missing_answer_404_with_a_message(_client, _auth_headers):
    res = await _client.get(f"{API}/benchmark/results")
    assert res.status_code == 404 and res.json()["detail"] == "No benchmark results found."
    res = await _client.get(f"{API}/benchmark/stats")
    assert res.json()["detail"] == "No stats results found. Run evaluation first."


# ---- database ------------------------------------------------------------------------------


def test_database_url_message_shows_an_example(monkeypatch):
    monkeypatch.setattr(database, "get_settings", lambda: types.SimpleNamespace(database_url=""))
    with pytest.raises(RuntimeError) as error:
        database.database_url()
    assert str(error.value) == (
        "DATABASE_URL is not set. Example: "
        "postgresql+psycopg://user:password@localhost:5432/hadith"
    )


async def test_sessions_keep_objects_usable_after_commit(_patched_paths):
    assert database.get_sync_session().expire_on_commit is False
    async with database.get_session() as session:
        assert session.sync_session.expire_on_commit is False


def test_now_iso_is_utc():
    assert database.now_iso().endswith("+00:00")


def test_books_and_the_flat_view_round_trip(_patched_paths):
    with database.get_sync_session() as session:
        database.insert_hadith_rows(
            session,
            [
                {"id": 70, "Book": "Abu Dawud", "LK_Book": "abudawud", "Chapter_Number": 4},
                {"id": 71, "Book": "Abu Dawud", "Chapter_Number": 4},
                {"id": 72, "Book": "Nasai", "Preprocessed_English_Matn": "text"},
            ],
        )
        session.commit()
    flat = database.read_hadiths_df().set_index("id")
    assert flat.loc[70, "LK_Book"] == "abudawud" and flat.loc[71, "LK_Book"] == "abudawud"
    assert pd.isna(flat.loc[72, "LK_Book"])
    assert flat.loc[72, "Preprocessed_English_Matn"] == "text"
    assert pd.isna(flat.loc[70, "Preprocessed_English_Matn"])
    assert database.get_hadith_row(70)["LK_Book"] == "abudawud"
    assert database.get_hadith_row(1)["Chapter_Title_English"] == "Prayer"


def test_the_flat_view_orders_by_id_and_honours_an_explicit_order(_patched_paths):
    assert database.read_hadiths_df()["id"].tolist() == [1, 2, 3]
    ordered = database.read_hadiths_df(order_by=database.Hadith.id.desc())
    assert ordered["id"].tolist() == [3, 2, 1]


def test_two_titles_error_names_the_chapter(_patched_paths):
    rows = [
        {"id": 80, "Book": "Tirmidhi", "Chapter_Number": 9, "Chapter_Title_English": "X"},
        {"id": 81, "Book": "Tirmidhi", "Chapter_Number": 9, "Chapter_Title_English": "Y"},
    ]
    with database.get_sync_session() as session:
        with pytest.raises(ValueError) as error:
            database.insert_hadith_rows(session, rows)
    assert str(error.value) == "Chapter 9 of Tirmidhi has two different titles"


def test_drop_corpus_tables_inside_a_caller_transaction_can_roll_back(_patched_paths):
    from sqlalchemy import inspect

    engine = database.get_sync_engine()
    with engine.connect() as conn:
        trans = conn.begin()
        database.drop_corpus_tables(conn)
        assert "hadiths" not in inspect(conn).get_table_names()
        trans.rollback()
    assert "hadiths" in inspect(engine).get_table_names()
    database.drop_corpus_tables()
    assert "hadiths" not in inspect(engine).get_table_names()


def test_annotation_model_is_importable_for_the_label_checks():
    assert Annotation.__tablename__ == "annotations"


async def test_benchmark_qrels_lists_every_query_with_empty_grades(_client):
    res = await _client.get(f"{API}/benchmark/qrels")
    body = res.json()
    assert res.status_code == 200
    assert body["qrels"] == {
        "q1": {"query": "prayer", "grades": {}},
        "q2": {"query": "fasting", "grades": {}},
        "q3": {"query": "zakat", "grades": {}},
    }
    assert "2000 hadiths" in body["description"]
    assert res.headers["cache-control"] == "public, max-age=300"


def test_the_search_context_gives_a_session_and_the_lazy_model_loader(_patched_paths):
    from routers.search import get_search_context
    from scripts import get_model

    generator = get_search_context()
    context = next(generator)
    assert context.model is get_model
    assert context.session.get(database.Hadith, 1).Book == "Bukhari"
    generator.close()
