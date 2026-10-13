import pytest

from quran.agent import Deps, answer_question
from quran.grounding import Evidence, GroundingError, check_draft, render
from quran.types import FALLBACK_SENTENCE, Filters, LLMError, NodeRef, SourceInfo, WebPassage

AYAH = "ٱللَّهُ لَآ إِلَٰهَ إِلَّا هُوَ ٱلْحَىُّ ٱلْقَيُّومُ"
SRC = SourceInfo("quran", "القرآن", ("ar",), ("surah",))


class _LLM:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    async def generate_json(self, prompt, schema):
        self.calls += 1
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def generate_text(self, prompt, max_tokens=256):
        """A scripted writer: a dict reply {lead_in, citations} becomes cited text, a str is as is."""
        reply = await self.generate_json(prompt, {})
        if isinstance(reply, dict):
            marks = " ".join(f"[{i}]" for i in reply.get("citations", []))
            return f"{reply.get('lead_in', '')} {marks}".strip()
        return reply


def _node():
    return NodeRef("quran", 1, "ayah", AYAH, surah=2, ayah_from=255, ayah_to=255, score=0.9)


async def _choose(llm, q, sources):
    return "quran"


async def _filters(llm, q, info):
    return Filters()


def _deps(draft, nodes=None, web=None, controller=None, **kw):
    async def retrieve(source, q, f):
        return [_node()] if nodes is None else nodes

    return Deps(
        controller=controller or _LLM({}),
        answerer=draft,
        retrieve=retrieve,
        sources=[SRC],
        web=web,
        choose_source=_choose,
        extract_filters=_filters,
        **kw,
    )


GOOD = {"lead_in": "آية الكرسي:", "citations": ["e1"]}


async def test_happy_path_inserts_verbatim_text():
    ans = await answer_question("ما آية الكرسي؟", _deps(_LLM(GOOD)))
    assert not ans.fallback
    assert ans.citations[0].text == AYAH
    assert ans.citations[0].ref == "quran:2:255"
    assert AYAH in ans.text


async def test_invented_id_revises_then_succeeds():
    bad = {"lead_in": "x", "citations": ["e9"]}
    llm = _LLM(bad, GOOD)
    ans = await answer_question("q", _deps(llm))
    assert not ans.fallback and llm.calls == 2
    assert "revise" in ans.trace


async def test_loop_cap_answers_from_the_evidence_when_the_model_cannot_format_a_draft():
    bad = {"lead_in": "x", "citations": ["e9"]}
    llm = _LLM(bad)
    ans = await answer_question("q", _deps(llm, max_loops=2))
    assert not ans.fallback and ans.citations  # extractive: evidence exists, only the model failed
    assert llm.calls == 3


async def test_cap_zero_no_revise():
    llm = _LLM({"lead_in": "x", "citations": ["e9"]})
    ans = await answer_question("q", _deps(llm, max_loops=0))
    assert not ans.fallback and ans.citations and llm.calls == 1


@pytest.mark.parametrize("err", [LLMError("timeout"), LLMError("invalid json")])
async def test_llm_error_in_draft_falls_back(err):
    ans = await answer_question("q", _deps(_LLM(err)))
    assert ans.fallback and ans.text == FALLBACK_SENTENCE


async def test_controller_error_falls_back():
    ans = await answer_question("q", _deps(_LLM(GOOD), controller=_LLM()))

    async def boom(llm, q, s):
        raise LLMError("down")

    deps = _deps(_LLM(GOOD))
    deps.choose_source = boom
    ans = await answer_question("q", deps)
    assert ans.fallback


async def test_no_evidence_no_web_falls_back():
    ans = await answer_question("q", _deps(_LLM(GOOD), nodes=[]))
    assert ans.fallback


async def test_no_evidence_web_empty_falls_back():
    async def web(q):
        return []

    ans = await answer_question("q", _deps(_LLM(GOOD), nodes=[], web=web))
    assert ans.fallback


async def test_no_evidence_web_used_and_labelled():
    async def web(q):
        return [WebPassage("https://quran.com/2/255", "نص من الويب")]

    draft = {"lead_in": "", "citations": ["w1"]}
    ans = await answer_question("q", _deps(_LLM(draft), nodes=[], web=web))
    assert not ans.fallback
    assert ans.citations[0].web and ans.citations[0].text == "نص من الويب"


async def test_low_score_hits_are_dropped():
    ans = await answer_question("q", _deps(_LLM(GOOD), min_score=0.99))
    assert ans.fallback


async def test_retrieval_exception_falls_back():
    deps = _deps(_LLM(GOOD))

    async def boom(*a):
        raise RuntimeError("db")

    deps.retrieve = boom
    assert (await answer_question("q", deps)).fallback


async def test_exhausted_loops_try_web_once():
    async def web(q):
        return [WebPassage("https://quran.com/x", "ويب")]

    bad = {"lead_in": "x", "citations": ["e9"]}
    llm = _LLM(bad, bad, bad, {"lead_in": "", "citations": ["w1"]})
    ans = await answer_question("q", _deps(llm, web=web, max_loops=2))
    assert not ans.fallback and "web: 1 passages" in ans.trace


def test_render_rejects_unknown_and_copies_text():
    ev = {"e1": Evidence("e1", "quran:1:1", "quran", AYAH)}
    assert render(["e1"], ev)[0].text == AYAH
    with pytest.raises(GroundingError):
        render(["e2"], ev)


@pytest.mark.parametrize(
    "draft",
    [None, {}, {"citations": []}, {"citations": ["e1", "e1"]}, {"citations": [1]},
     {"citations": ["e1"], "lead_in": "x" * 1600}],
)  # fmt: skip
def test_check_draft_rejects_malformed(draft):
    ev = {"e1": Evidence("e1", "r", "s", "t")}
    assert check_draft(draft, ev)


def _shown():
    return {
        "e1": Evidence("e1", "quran:2:255", "quran", AYAH),
        "e2": Evidence("e2", "q:1:1", "quran", "ب"),
    }


def test_parse_written_keeps_cited_sentences_and_labels_the_refs():
    from quran.grounding import parse_written

    raw = "هذه آية الكرسي [e1]. جملة بلا مصدر. وهي أعظم آية [e1][e2]."
    draft = parse_written(raw, _shown())
    assert draft == {
        "lead_in": "هذه آية الكرسي [quran:2:255]. وهي أعظم آية [quran:2:255][q:1:1].",
        "citations": ["e1", "e2"],
    }


@pytest.mark.parametrize(
    "raw",
    [
        "جملة بلا مصدر.",  # nothing cited
        "الإجابة هي [e9].",  # a passage the model was not shown
        "This is the answer [e1].",  # not Arabic
        "الصيام fasting واجب [e1].",  # a foreign word inside Arabic text
        "لا_توجد_معلومات [e1]",  # the writer declined
        "قال تعالى ﴿ما ليس في المصادر﴾ [e1].",  # a made-up quotation
        None,
    ],
)
def test_parse_written_rejects_untrustworthy_text(raw):
    from quran.grounding import parse_written

    assert parse_written(raw, _shown()) is None


def test_parse_written_accepts_a_real_quotation_ignoring_diacritics():
    from quran.grounding import parse_written

    raw = "قال تعالى ﴿ٱللَّهُ لَآ إِلَٰهَ إِلَّا هُوَ﴾ [e1]."
    assert parse_written(raw, _shown())["citations"] == ["e1"]


async def test_writer_saying_no_information_gives_the_fallback():
    ans = await answer_question("ما؟", _deps(_LLM("لا_توجد_معلومات")))
    assert ans.fallback and ans.text == FALLBACK_SENTENCE


async def test_writers_answer_leads_and_the_verse_follows_verbatim():
    ans = await answer_question("ما؟", _deps(_LLM("هذه آية الكرسي [e1].")))
    assert ans.text.startswith("هذه آية الكرسي [quran:2:255].") and AYAH in ans.text
    assert ans.citations[0].text == AYAH


def _nodes(*texts):
    return [
        NodeRef("quran", i, "ayah", t, surah=2, ayah_from=i, ayah_to=i)
        for i, t in enumerate(texts, 1)
    ]


def _scorer(scores):
    async def rerank(question, texts):
        assert len(texts) == len(scores)
        return scores

    return rerank


async def test_rerank_reorders_the_evidence_and_renumbers_ids():
    deps = _deps(_LLM(GOOD), nodes=_nodes("first", "second"), rerank=_scorer([0.1, 0.9]))
    ans = await answer_question("q", deps)
    assert ans.citations[0].text == "second" and ans.citations[0].ref == "quran:2:2"
    assert any(step.startswith("rerank: 2/2 kept, top 0.90") for step in ans.trace)


async def test_rerank_drops_candidates_below_the_threshold():
    deps = _deps(
        _LLM(GOOD), nodes=_nodes("a", "b"), rerank=_scorer([0.05, 0.6]), rerank_min_score=0.3
    )
    ans = await answer_question("q", deps)
    assert [c.text for c in ans.citations] == ["b"]
    assert "rerank: 1/2 kept, top 0.60" in ans.trace


async def test_rerank_with_nothing_relevant_goes_to_web_search_or_the_fallback():
    deps = _deps(_LLM(GOOD), nodes=_nodes("a"), rerank=_scorer([0.1]), rerank_min_score=0.5)
    ans = await answer_question("q", deps)
    assert ans.fallback and ans.text == FALLBACK_SENTENCE


async def test_a_failing_reranker_keeps_the_retrieval_order():
    async def broken(question, texts):
        raise RuntimeError("model missing")

    deps = _deps(_LLM(GOOD), nodes=_nodes("a", "b"), rerank=broken)
    ans = await answer_question("q", deps)
    assert not ans.fallback and ans.citations[0].text == "a"
    assert "rerank error: RuntimeError" in ans.trace


async def test_only_the_candidate_cap_is_scored():
    deps = _deps(
        _LLM(GOOD), nodes=_nodes("a", "b", "c"), rerank=_scorer([0.2, 0.8]), rerank_candidates=2
    )
    ans = await answer_question("q", deps)
    assert ans.citations[0].text == "b"
