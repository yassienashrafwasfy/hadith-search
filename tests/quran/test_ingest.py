import pytest
from sqlalchemy import select

from quran import ingest, summarize
from quran.db import add_nodes, create_source
from quran.llm import FakeLLM
from quran.models import node_table
from quran.types import Level, LLMError, SourceInfo

INFO = SourceInfo("quran", "قرآن", ("ar",), ("surah",))


def _strip(text: str) -> str:
    return text.replace("ٱ", "ا")


def _prepare(session, corpus):
    create_source(session, INFO)
    add_nodes(
        session,
        "quran",
        [
            {
                "level": Level.AYAH,
                "text": a.text,
                "search_text": "",
                "surah": a.surah,
                "ayah_from": a.ayah,
                "ayah_to": a.ayah,
                "juz": a.juz,
                "place": a.place,
            }
            for a in corpus
        ],
    )
    return node_table("quran")


def _rows(session, table, level):
    return session.execute(
        select(table).where(table.c.level == level.value).order_by(table.c.id)
    ).all()


def _build(session, table, encoder, llm, **kw):
    return ingest.build_source(session, table, encoder, llm, normalise=_strip, **kw)


def test_build_creates_hierarchy_and_keeps_original_text(
    _quran_empty_session, _mini_corpus, _fake_encoder
):
    session = _quran_empty_session
    table = _prepare(session, _mini_corpus)
    llm = FakeLLM(text_responses=["ملخص"], repeat_last=True)
    stats = _build(session, table, _fake_encoder, llm, min_tokens=1, max_tokens=6)
    assert stats.ayat == len(_mini_corpus) and stats.surahs == 3
    surahs, passages = _rows(session, table, Level.SURAH), _rows(session, table, Level.PASSAGE)
    assert stats.passages == len(passages) > 3
    assert all(p.parent_id in {s.id for s in surahs} for p in passages)
    assert all(s.summary == "ملخص" and s.embedding is not None for s in surahs + passages)
    ayat = _rows(session, table, Level.AYAH)
    assert [a.text for a in ayat] == [a.text for a in _mini_corpus]
    pids = {p.id: p for p in passages}
    for a in ayat:
        parent = pids[a.parent_id]
        assert parent.ayah_from <= a.ayah_from <= parent.ayah_to and parent.surah == a.surah
        assert a.text in parent.text and a.summary is None and a.embedding is not None
        assert a.search_text == _strip(a.text)


def test_never_splits_an_ayah_and_covers_all(_quran_empty_session, _mini_corpus, _fake_encoder):
    session = _quran_empty_session
    table = _prepare(session, _mini_corpus)
    _build(session, table, _fake_encoder, None, min_tokens=1, max_tokens=4)
    for surah in (1, 112, 114):
        spans = [
            (p.ayah_from, p.ayah_to)
            for p in _rows(session, table, Level.PASSAGE)
            if p.surah == surah
        ]
        covered = [n for a, b in sorted(spans) for n in range(a, b + 1)]
        assert covered == sorted(set(covered))
        assert covered == [a.ayah for a in _mini_corpus if a.surah == surah]


def test_summary_failure_leaves_null_and_embeds_text(
    _quran_empty_session, _mini_corpus, _fake_encoder
):
    session = _quran_empty_session
    table = _prepare(session, _mini_corpus)
    llm = FakeLLM(text_responses=[LLMError("down")], repeat_last=True)
    stats = _build(session, table, _fake_encoder, llm)
    assert stats.summarised == 0
    passage = _rows(session, table, Level.PASSAGE)[0]
    assert passage.summary is None
    assert list(passage.embedding) == pytest.approx(_fake_encoder.encode([passage.search_text])[0])


def test_summary_is_embedded_not_text(_quran_empty_session, _mini_corpus, _fake_encoder):
    session = _quran_empty_session
    table = _prepare(session, _mini_corpus)
    _build(session, table, _fake_encoder, FakeLLM(text_responses=["موجز"], repeat_last=True))
    passage = _rows(session, table, Level.PASSAGE)[0]
    assert list(passage.embedding) == pytest.approx(_fake_encoder.encode(["موجز"])[0])
    assert "موجز" not in passage.text


def test_rebuild_replaces_derived_nodes(_quran_empty_session, _mini_corpus, _fake_encoder):
    session = _quran_empty_session
    table = _prepare(session, _mini_corpus)
    first = _build(session, table, _fake_encoder, None)
    second = _build(session, table, _fake_encoder, None)
    assert first == second
    assert len(_rows(session, table, Level.AYAH)) == len(_mini_corpus)
    assert len(_rows(session, table, Level.SURAH)) == 3


def test_summarise_prompt_is_truncated_and_empty_answer_is_none():
    import asyncio

    llm = FakeLLM(text_responses=["  "])
    assert asyncio.run(summarize.summarise(llm, "ا" * 20000)) is None
    assert len(llm.prompts[0]) < summarize.MAX_INPUT_CHARS + 300


def test_summarise_many_keeps_order():
    import asyncio

    llm = FakeLLM(text_responses=["a", LLMError("x"), "c"])
    got = asyncio.run(summarize.summarise_many(llm, ["1", "2", "3"], "surah"))
    assert got == ["a", None, "c"]


def test_script_parser_and_counter():
    from scripts import quran_build

    args = quran_build.build_parser().parse_args(["--source", "quran", "--no-summaries"])
    assert args.no_summaries and args.max_tokens == 200
    assert quran_build.token_counter(object())("a b c") == 3
