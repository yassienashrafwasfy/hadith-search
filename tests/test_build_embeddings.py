import numpy as np
import pandas as pd
import pytest

from scripts import build_embeddings as be


def test_has_text_and_clean_text():
    assert be.has_text("x") and not be.has_text(None) and not be.has_text("nan")
    assert be.clean_text("  hi ") == "hi" and be.clean_text(None) == ""


def test_normalize_arabic_passage_strips_diacritics():
    assert be.normalize_arabic_passage("الصَّلَاة") == "الصلاه"


def test_passages_prefix_and_prepare():
    df = pd.DataFrame({"id": [1, 2], "English_Matn": [" a ", "b"]})
    assert be._passages(df, "English", str) == ["passage: a", "passage: b"]
    assert be._passages(df, "English", str.upper) == ["passage: A", "passage: B"]


def test_passages_reject_missing_matn():
    df = pd.DataFrame({"id": [7], "Arabic_Matn": [None]})
    with pytest.raises(ValueError, match="Hadith id 7 is missing Arabic_Matn"):
        be._passages(df, "Arabic", str)


class _Model:
    def __init__(self, rows):
        self.rows = rows

    def encode(self, texts, batch_size, show_progress_bar):
        assert batch_size == be.EMBEDDING_BATCH_SIZE and show_progress_bar
        return np.ones((self.rows, 2))


def test_encode_and_save_stores_vectors(_patched_paths):
    import database
    from models import HadithEmbedding

    out = be._encode_and_save(_Model(2), ["a", "b"], "EN", [1, 3])
    assert out.shape == (2, 2)
    with database.get_sync_session() as session:
        rows = {r.hadith_id: r for r in session.query(HadithEmbedding)}
    assert sorted(rows) == [1, 3]
    assert list(rows[1].english) == [1.0, 1.0] and rows[1].arabic is None


def test_storing_one_language_keeps_the_other(_patched_paths):
    import database
    from models import HadithEmbedding

    be._encode_and_save(_Model(1), ["a"], "EN", [1])
    be._encode_and_save(_Model(1), ["a"], "AR", [1])
    be._encode_and_save(_Model(1), ["a"], "EN", [1])  # re-running replaces, not duplicates
    with database.get_sync_session() as session:
        (row,) = session.query(HadithEmbedding).all()
    assert row.english is not None and row.arabic is not None


def test_encode_and_save_detects_count_mismatch():
    with pytest.raises(ValueError, match="AR embedding count mismatch: 1 embeddings for 3 rows"):
        be._encode_and_save(_Model(1), ["a"], "AR", [1, 2, 3])


def test_choose_device(monkeypatch):
    monkeypatch.setattr(be.torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert be._choose_device() == "cpu"
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert be._choose_device() is None
    monkeypatch.setattr(be.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(be.torch.cuda, "get_device_name", lambda i: "GPU")
    assert be._choose_device() == "cuda"


def test_load_corpus_rejects_missing_matn(_patched_paths, monkeypatch):
    import database
    from models import Hadith

    assert len(be._load_corpus()) == 3
    with database.get_sync_session() as session:
        session.get(Hadith, 2).English_Matn = ""
        session.commit()
    with pytest.raises(ValueError, match=r"English ids=\[2\]"):
        be._load_corpus()


def test_run_end_to_end_with_fake_model(_patched_paths, monkeypatch):
    import database
    from models import HadithEmbedding

    monkeypatch.setattr(be, "_choose_device", lambda: "cpu")
    monkeypatch.setattr(be, "SentenceTransformer", lambda name, device: _Model(3))
    be.run()
    with database.get_sync_session() as session:
        rows = session.query(HadithEmbedding).order_by(HadithEmbedding.hadith_id).all()
    assert [r.hadith_id for r in rows] == [1, 2, 3]
    assert all(r.english is not None and r.arabic is not None for r in rows)


def test_run_aborts_without_device(monkeypatch):
    monkeypatch.setattr(be, "_choose_device", lambda: None)
    monkeypatch.setattr(be, "_load_corpus", lambda: pytest.fail("must not load"))
    be.run()
