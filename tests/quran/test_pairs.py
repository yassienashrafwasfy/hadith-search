import json

from scripts.quran_pairs import corpus_rows, read_holdout, write_jsonl


def test_rows_keep_the_original_text_and_flag_held_out(_quran_session, _mini_corpus):
    rows = list(corpus_rows(_quran_session, ["quran"], {(112, 2)}))
    assert len(rows) == len(_mini_corpus)
    by_ref = {(r["surah"], r["ayah"]): r for r in rows}
    original = next(a.text for a in _mini_corpus if (a.surah, a.ayah) == (112, 2))
    assert by_ref[(112, 2)]["text"] == original
    assert by_ref[(112, 2)]["encoding"] != original
    assert [r["held_out"] for r in rows if (r["surah"], r["ayah"]) != (112, 2)] == [False] * (
        len(rows) - 1
    )
    assert by_ref[(112, 2)]["held_out"] is True


def test_rows_are_ordered_and_only_ayat(_quran_session):
    rows = list(corpus_rows(_quran_session, ["quran"], set()))
    refs = [(r["surah"], r["ayah"]) for r in rows]
    assert refs == sorted(refs) and len(set(refs)) == len(refs)


def test_tafsir_rows_carry_their_source(_quran_session):
    rows = list(corpus_rows(_quran_session, ["tafsir_mini"], set()))
    assert rows and {r["source"] for r in rows} == {"tafsir_mini"}


def test_holdout_file_and_jsonl_round_trip(tmp_path):
    path = tmp_path / "holdout.json"
    path.write_text("[[2, 255], [112, 1]]", encoding="utf-8")
    assert read_holdout(str(path)) == {(2, 255), (112, 1)}
    assert read_holdout(None) == set()
    out = tmp_path / "sub" / "corpus.jsonl"
    assert write_jsonl([{"text": "قُلْ"}], str(out)) == 1
    assert json.loads(out.read_text(encoding="utf-8")) == {"text": "قُلْ"}


def _hadith_frame():
    import pandas as pd

    return pd.DataFrame(
        {
            "id": [3, 1, 2, 4],
            "Book": ["B", "A", "A", "A"],
            "Chapter_Number": [1, 2, 2, 1],
            "Arabic_Matn": ["ج", "أ", None, "د"],
            "Chapter_Title_Arabic": ["ت1", "ت2", "ت2", None],
        }
    )


def test_hadith_rows_number_chapters_and_skip_empty_matn():
    from scripts.quran_pairs import hadith_rows

    rows = sorted(hadith_rows(_hadith_frame(), str.upper), key=lambda r: r["ayah"])
    assert [r["ayah"] for r in rows] == [1, 3, 4]  # id 2 has no matn
    by_id = {r["ayah"]: r for r in rows}
    # chapters sorted: (A,1)=1, (A,2)=2, (B,1)=3
    assert (by_id[4]["surah"], by_id[1]["surah"], by_id[3]["surah"]) == (1, 2, 3)
    assert by_id[1]["chapter_title"] == "ت2" and by_id[4]["chapter_title"] == ""
    assert by_id[3]["source"] == "hadith" and by_id[3]["text"] == "ج"
    assert by_id[3]["encoding"] == "ج".upper() and not by_id[3]["held_out"]
