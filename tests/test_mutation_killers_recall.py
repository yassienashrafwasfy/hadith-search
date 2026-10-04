"""recall_proxy.evaluate/run/main with a fake search system (no model, no corpus build)."""

import json
import random
import re
import types

import pytest
from sqlalchemy import select

import database
from scripts import recall_proxy as rp

_MODEL = object()
LONG = " ".join(f"كلمه{i}" for i in range(14))


def _system(ranking):
    return types.SimpleNamespace(run=lambda _ctx, query, lang: ranking[query])


def test_evaluate_plain_and_capped_recall():
    queries = [("a", {1, 2, 3, 4}), ("b", {9})]
    system = _system({"a": {1: 0.9, 7: 0.8, 2: 0.7}, "b": {5: 0.5, 9: 0.4}})
    plain = rp.evaluate(system, None, queries, [1, 2], capped=False)
    assert plain == {
        "k=1": {"recall": (1 / 4 + 0) / 2},
        "k=2": {"recall": (1 / 4 + 1 / 1) / 2},
    }
    capped = rp.evaluate(system, None, queries, [1, 2], capped=True)
    assert capped == {
        "k=1": {"capped_recall": (1 / 1 + 0) / 2},
        "k=2": {"capped_recall": (1 / 2 + 1 / 1) / 2},
    }


def test_evaluate_asks_for_arabic_queries():
    seen = []
    system = types.SimpleNamespace(run=lambda _c, query, lang: seen.append((query, lang)) or {})
    rp.evaluate(system, None, [("q", {1})], [3], capped=False)
    assert seen == [("q", "AR")]


def test_the_known_item_sample_is_seeded_and_limited():
    texts = {i: LONG + f" x{i}" for i in range(10)}
    first = rp.known_item_queries(texts, 3, seed=5)
    assert len(first) == 3 and first == rp.known_item_queries(texts, 3, seed=5)
    assert rp.known_item_queries({1: " ".join(["w"] * 11)}, 5, seed=1) == []
    assert len(rp.known_item_queries({1: " ".join(["w"] * 12)}, 5, seed=1)) == 1


def test_chapter_queries_use_the_encoder_cleanup():
    ((query, ids),) = rp.chapter_queries([(1, "الصَّلاة")])
    assert query == rp.encoding_text("الصَّلاة") and ids == {1}


def _add_a_long_hadith():
    with database.get_sync_session() as session:
        database.insert_hadith_rows(
            session,
            [{"id": 90, "Book": "Tirmidhi", "Arabic_Matn": LONG, "Chapter_Number": 90}],
        )
        session.commit()


def _fake_run(monkeypatch, seen=None):
    def _search(ctx, query, lang):
        if seen is not None:
            seen.append(ctx)
        return {1: 1.0}

    perfect = types.SimpleNamespace(run=_search)
    monkeypatch.setattr(rp, "SYSTEMS", {slug: perfect for slug in rp.METHODS})
    monkeypatch.setattr(rp, "get_model", lambda: _MODEL)


def test_run_builds_the_report_and_writes_it_as_utf8_json(_patched_paths, monkeypatch, tmp_path):
    _add_a_long_hadith()
    _fake_run(monkeypatch)
    out = tmp_path / "report.json"
    report = rp.run([1, 2], 5, 7, str(out))
    assert report["seed"] == 7 and report["ks"] == [1, 2]
    assert report["corpus_hadiths"] == 4
    assert report["known_item_queries"] == 1
    assert report["chapter_queries"] == 3
    assert list(report["methods"]) == list(rp.METHODS)
    for result in report["methods"].values():
        assert set(result) == {"known_item", "chapter"}
        assert set(result["known_item"]) == {"k=1", "k=2"}
        assert "recall" in result["known_item"]["k=1"]
        assert "capped_recall" in result["chapter"]["k=1"]
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved == report
    assert out.read_text(encoding="utf-8").startswith('{\n  "seed": 7')


def test_run_hands_every_system_a_live_context_and_reports_timings(
    _patched_paths, monkeypatch, tmp_path, capsys
):
    seen = []
    _add_a_long_hadith()
    _fake_run(monkeypatch, seen)
    rp.run([1], 5, 7, str(tmp_path / "r.json"))
    assert seen and all(ctx.model() is _MODEL for ctx in seen)
    assert all(ctx.session.execute(select(1)).scalar() == 1 for ctx in seen[:1])
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(":")[0] for line in lines] == list(rp.METHODS)
    assert all(re.fullmatch(r"[a-z-]+: \d+s", line) for line in lines)


def test_the_known_item_sample_uses_the_given_seed():
    texts = {i: LONG + f" x{i}" for i in range(10)}
    ids = sorted(texts)
    expected = [texts[i].split() for i in random.Random(5).sample(ids, 3)]
    got = [q.split() for q, _ in rp.known_item_queries(texts, 3, seed=5)]
    assert got == [words[: len(words) // 2] for words in expected]


def test_main_passes_its_options_to_run(monkeypatch, capsys):
    calls = []

    def _run(ks, n, seed, out):
        calls.append((ks, n, seed, out))
        return {"ks": ks, "known_item_queries": 0, "chapter_queries": 0, "methods": {}}

    monkeypatch.setattr(rp, "run", _run)
    rp.main([])
    rp.main(["--k", "1", "5", "--n", "9", "--seed", "3", "--out", "o.json"])
    assert calls == [([3, 8], 1000, 42, rp.DEFAULT_OUT), ([1, 5], 9, 3, "o.json")]
    assert rp.DEFAULT_OUT.endswith("data/recall_proxy.json")
    out = capsys.readouterr().out
    assert "known_item (0 queries)" in out and "chapter (0 queries)" in out


def test_print_report_counts_each_test_with_its_own_query_total(capsys):
    cell = {"k=3": {"recall": 0.5}}
    rp.print_report(
        {
            "ks": [3],
            "known_item_queries": 11,
            "chapter_queries": 22,
            "methods": {"m": {"known_item": cell, "chapter": {"k=3": {"capped_recall": 1.0}}}},
        }
    )
    out = capsys.readouterr().out
    assert "known_item (11 queries)" in out and "chapter (22 queries)" in out
    assert "  m                  recall@3 0.500" in out


def test_main_help_names_the_script(capsys):
    with pytest.raises(SystemExit):
        rp.main(["--help"])
    out = capsys.readouterr().out
    assert "Recall@k of the Arabic semantic methods" in out
    assert "Neither test" not in out  # only the first docstring line is the description
