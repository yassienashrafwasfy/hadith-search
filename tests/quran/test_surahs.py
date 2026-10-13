import pytest

from quran.surahs import SURAH_NAMES, find_surah


def test_there_are_114_names():
    assert len(SURAH_NAMES) == 114


@pytest.mark.parametrize(
    "question, number",
    [
        ("تفسير الميسر لسورة الإخلاص", 112),
        ("ما معنى سورة آل عمران؟", 3),
        ("اقرأ سورة الفاتحة", 1),
        ("سورة ق", 50),
    ],
)
def test_named_surah_is_found(question, number):
    assert find_surah(question)[0] == number


def test_a_surah_name_without_the_word_surah_is_ordinary():
    assert find_surah("ماذا عن النور") is None
