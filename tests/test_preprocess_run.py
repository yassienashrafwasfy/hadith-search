import json

import pandas as pd
import pytest

import database
from scripts import preprocess as p


def test_english_texts_skip_empty(monkeypatch):
    monkeypatch.setattr(p, "preprocess_english", lambda t: t.upper())
    assert p._preprocess_english_texts(["a", "", None]) == ["A", "", ""]


def test_arabic_texts_parallel(monkeypatch):
    monkeypatch.setattr(p, "preprocess_arabic", lambda t: t + "!")
    assert p._preprocess_arabic_texts(["a", "", "b"]) == (["a!", "", "b!"], "parallel")


def test_arabic_texts_fall_back_to_sequential(monkeypatch):
    calls = []

    def flaky(t):
        calls.append(t)
        if len(calls) == 1:
            raise RuntimeError("pool broke")
        return t + "!"

    monkeypatch.setattr(p, "preprocess_arabic", flaky)
    assert p._preprocess_arabic_texts(["a", "b"]) == (["a!", "b!"], "sequential")


def test_preprocess_column_dispatches_by_language(monkeypatch, capsys):
    monkeypatch.setattr(p, "preprocess_english", lambda t: "en:" + t)
    monkeypatch.setattr(p, "preprocess_arabic", lambda t: "ar:" + t)
    df = pd.DataFrame({"English_Text": ["x", None], "Arabic_Text": ["y", "z"]})
    assert p._preprocess_column(df, "en", "English_Text", "EN") == ["en:x", ""]
    assert p._preprocess_column(df, "ar", "Arabic_Text", "AR") == ["ar:y", "ar:z"]
    out = capsys.readouterr().out
    assert "(parallel)" in out and "Preprocessing en..." in out


def test_empty_ids_and_drop_rows():
    df = pd.DataFrame({"id": [1, 2, 3], "Book": ["a", "b", "c"], "Hadith_Number": [1, 2, 3]})
    assert p._empty_ids(df, ["x", "", "nan"]) == {2, 3}
    rows = p._drop_rows(df, {2})
    assert rows == [
        {
            "id_before_drop": 2,
            "LK_Book": "b",
            "Book": "b",
            "Hadith_Number": 2,
            "Chapter_Number": "",
        }
    ]


def test_load_drop_audit_default_and_existing(tmp_path):
    fresh = p._load_drop_audit(str(tmp_path / "none.json"))
    assert fresh["count"] == 0 and fresh["second_stage"] is None
    existing = tmp_path / "a.json"
    existing.write_text(json.dumps({"reason": "r", "count": 5}))
    assert p._load_drop_audit(str(existing)) == {"reason": "r", "count": 5}


def test_write_drop_audit_records_counts(tmp_path):
    df = pd.DataFrame({"id": [1, 2], "Book": ["a", "b"]})
    path = tmp_path / "audit.json"
    p._write_drop_audit(str(path), df, {1}, {2})
    audit = json.loads(path.read_text())
    stage = audit["second_stage"]
    assert (stage["count"], stage["count_en"], stage["count_ar"]) == (2, 1, 1)
    assert [r["id_before_drop"] for r in stage["rows"]] == [1, 2]


def test_build_updates_skips_dropped():
    df = pd.DataFrame({"id": [1, 2]})
    results = {"Preprocessed_English": ["a", "b"], "Preprocessed_Arabic": ["c", "d"]}
    assert p._build_updates(df, results, {1}) == [
        {"id": 2, "Preprocessed_English": "b", "Preprocessed_Arabic": "d"}
    ]


def test_report_drops_prints_samples(capsys):
    p._report_drops("/x.json", {1, 2}, set())
    out = capsys.readouterr().out
    assert "Dropped 2 hadiths" in out and "EN=2, AR=0" in out
    assert "Sample English-empty IDs: [1, 2]" in out and "Arabic-empty" not in out


def test_run_drops_rows_with_empty_matn(_patched_paths, monkeypatch, tmp_path):
    monkeypatch.setattr(p, "DATA_DIR", str(_patched_paths))
    monkeypatch.setattr(p, "preprocess_english", lambda t: "" if "fasting is a shield" == t else t)
    monkeypatch.setattr(p, "preprocess_arabic", lambda t: t)
    p.run()
    assert database.get_hadith_row(2) is None
    assert database.get_hadith_row(1) is not None
    audit = json.loads((_patched_paths / "dropped_lk_rows.json").read_text())
    assert audit["second_stage"]["count_en"] == 1
    assert [r["id_before_drop"] for r in audit["second_stage"]["rows"]] == [2]


def test_delete_hadiths_batches(_patched_paths):
    with database.get_sync_session() as session:
        p._delete_hadiths(session, [1, 3])
        session.commit()
    assert database.read_hadiths_df()["id"].tolist() == [2]


@pytest.mark.parametrize("ids", [[], [2]])
def test_drop_empty_matn_noop_when_nothing_empty(_patched_paths, ids):
    df = database.read_hadiths_df()
    results = {
        "Preprocessed_English_Matn": ["x", "x", "x"],
        "Preprocessed_Arabic_Matn": ["x", "x" if not ids else "", "x"],
    }
    with database.get_sync_session() as session:
        dropped = p._drop_empty_matn(session, df, results, str(_patched_paths))
    assert dropped == set(ids)
