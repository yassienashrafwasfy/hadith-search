import pytest

from quran.llm import FakeLLM
from quran.router import choose_source, pick_with_llm
from quran.selfquery import extract_filters
from quran.types import Filters, LLMError, Place, SourceInfo

SRC = SourceInfo(
    name="ibn_kathir",
    description="تفسير",
    languages=("ar",),
    fields=("surah", "ayah_from", "ayah_to", "juz", "place", "author"),
    authors=("ابن كثير",),
)


async def _filters(raw, source=SRC):
    return await extract_filters(FakeLLM(json_responses=[raw]), "سؤال", source)


async def test_valid_filters_pass():
    raw = {"surah": 2, "ayah_from": 255, "ayah_to": 256, "juz": 3, "place": "medinan"}
    raw["author"] = "ابن كثير"
    assert await _filters(raw) == Filters(2, 255, 256, 3, Place.MEDINAN, "ابن كثير")


@pytest.mark.parametrize(
    "raw",
    [
        {"surah": 115},
        {"surah": 0},
        {"surah": "2"},
        {"surah": True},
        {"juz": 31},
        {"place": "mars"},
        {"author": "مجهول"},
        {"ayah_from": 5},
        {"invented": 1},
    ],
)
async def test_invalid_fields_are_dropped(raw):
    assert await _filters(raw) == Filters()


async def test_reversed_range_drops_range_only():
    raw = {"surah": 2, "ayah_from": 5, "ayah_to": 3}
    assert await _filters(raw) == Filters(surah=2)


async def test_one_bad_field_keeps_the_good_ones():
    assert await _filters({"surah": 2, "juz": 99}) == Filters(surah=2)


async def test_fields_the_source_cannot_filter_are_dropped():
    src = SourceInfo("quran", "d", ("ar",), ("surah",))
    assert await _filters({"surah": 2, "juz": 3}, src) == Filters(surah=2)


async def test_llm_error_gives_empty_filters():
    assert await extract_filters(FakeLLM(json_responses=[LLMError("x")]), "q", SRC) == Filters()


async def test_non_dict_answer_gives_empty_filters():
    assert await _filters(["surah"]) == Filters()


SOURCES = [SRC, SourceInfo("quran", "نص القرآن", ("ar",), ())]


async def test_choose_source_known():
    llm = FakeLLM(json_responses=[{"source": "quran"}])
    assert await pick_with_llm(llm, "q", SOURCES) == "quran"


@pytest.mark.parametrize("raw", [{"source": "nope"}, {"source": None}, {}, {"source": 3}])
async def test_choose_source_unknown_is_none(raw):
    assert await pick_with_llm(FakeLLM(json_responses=[raw]), "q", SOURCES) is None


async def test_choose_source_error_or_empty_catalog():
    assert await pick_with_llm(FakeLLM(json_responses=[LLMError("x")]), "q", SOURCES) is None
    assert await pick_with_llm(FakeLLM(), "q", []) is None


TAFSIRS = [
    SourceInfo("quran", "نص القرآن", ("ar",), ("surah",)),
    SourceInfo("tafsir_ibn_kathir", "d", ("ar",), ("author",), ("ابن كثير",)),
    SourceInfo("tafsir_muyassar", "d", ("ar",), ("author",), ("الميسر",)),
]


@pytest.mark.parametrize(
    "question, expected",
    [
        ("ماذا قال ابن كثير في آية الكرسي؟", "tafsir_ibn_kathir"),
        ("تفسير الميسر لسورة الإخلاص", "tafsir_muyassar"),
        ("ما هو نص سورة الفاتحة؟", "quran"),
        ("اشرح معنى آية الدين", "tafsir_muyassar"),
        ("آيات الصبر", "quran"),
    ],
)
async def test_rules_route_before_the_controller(question, expected):
    llm = FakeLLM(json_responses=[{"source": "quran"}])
    assert await choose_source(llm, question, TAFSIRS) == expected
    assert llm.prompts == []  # the controller was not needed
