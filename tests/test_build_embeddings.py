import numpy as np
import pandas as pd
import pytest

from scripts import build_embeddings as be


def test_has_text_and_clean_text():
    assert be.has_text("x") and not be.has_text(None) and not be.has_text("nan")
    assert be.clean_text("  hi ") == "hi" and be.clean_text(None) == ""


def test_passages_strip_diacritics_and_add_no_prefix():
    df = pd.DataFrame({"id": [1], "Arabic_Matn": ["  الصَّلَاة   خير "]})
    assert be._passages(df) == ["الصلاة خير"]


def test_passages_reject_missing_matn():
    df = pd.DataFrame({"id": [7], "Arabic_Matn": [None]})
    with pytest.raises(ValueError, match="Hadith id 7 is missing Arabic_Matn"):
        be._passages(df)


class _Model:
    def __init__(self, rows):
        self.rows = rows

    def encode(self, texts):
        return np.ones((self.rows, 2))


def test_encode_and_save_stores_arabic_vectors(_patched_paths):
    import database
    from models import HadithEmbedding

    out = be._encode_and_save(_Model(2), ["a", "b"], [1, 3])
    assert out.shape == (2, 2)
    with database.get_sync_session() as session:
        rows = {r.hadith_id: r for r in session.query(HadithEmbedding)}
    assert sorted(rows) == [1, 3]
    assert list(rows[1].arabic) == [1.0, 1.0] and rows[1].english is None


def test_rerunning_replaces_rather_than_duplicates(_patched_paths):
    import database
    from models import HadithEmbedding

    be._encode_and_save(_Model(1), ["a"], [1])
    be._encode_and_save(_Model(1), ["a"], [1])
    with database.get_sync_session() as session:
        assert session.query(HadithEmbedding).count() == 1


def test_encode_and_save_detects_count_mismatch():
    with pytest.raises(ValueError, match="AR embedding count mismatch: 1 embeddings for 3 rows"):
        be._encode_and_save(_Model(1), ["a"], [1, 2, 3])


def test_load_corpus_rejects_missing_matn(_patched_paths):
    import database
    from models import Hadith

    assert len(be._load_corpus()) == 3
    with database.get_sync_session() as session:
        session.get(Hadith, 2).Arabic_Matn = ""
        session.commit()
    with pytest.raises(ValueError, match=r"ids=\[2\]"):
        be._load_corpus()


def test_run_end_to_end_with_fake_model(_patched_paths, monkeypatch):
    import database
    from models import HadithEmbedding

    monkeypatch.setattr(be, "load_encoder", lambda threads: _Model(3))
    be.run()
    with database.get_sync_session() as session:
        rows = session.query(HadithEmbedding).order_by(HadithEmbedding.hadith_id).all()
    assert [r.hadith_id for r in rows] == [1, 2, 3]
    assert all(r.arabic is not None and r.english is None for r in rows)
