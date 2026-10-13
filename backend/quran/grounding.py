"""Deterministic grounding: the LLM names evidence ids, code inserts the verbatim text."""

import re
from dataclasses import dataclass

from quran.types import Citation, NodeRef

MAX_LEAD_IN_CHARS = 1500  # a written answer, with each [e1] already replaced by a longer ref label
MAX_CITATIONS = 5
KEPT_CITATIONS = 3
PROMPT_PASSAGES = 3  # passages the model sees: more only slows a CPU-bound model down
MIN_ARABIC_SHARE = 0.6  # of the letters; a small model drifts into English or Chinese otherwise
NO_INFO = "لا_توجد_معلومات"
_MARKER = re.compile(r"\[((?:e|w)\d+)\]")
_SENTENCE_END = re.compile(r"(?<=[.؟!\n])\s*")
_QUOTE = re.compile("﴿([^﴾]+)﴾")
_LABEL = re.compile(r"\[[^\]]*\]")
_LATIN = re.compile(r"[A-Za-z]")
_LETTER = re.compile(r"[^\W\d_]")
_ARABIC_LETTER = re.compile("[\u0621-\u064a]")
_MARKS = re.compile("[\u0640\u064b-\u065f\u0670\u06d6-\u06ed]")
DEFAULT_LEAD_IN = "فيما يلي ما ورد في القرآن الكريم وكتب التفسير في سؤالك:"


class GroundingError(Exception):
    pass


@dataclass(frozen=True)
class Evidence:
    """One retrieved passage the model may cite by `id` (e1, e2 ... local; w1 ... web)."""

    id: str
    ref: str
    source: str
    text: str
    web: bool = False
    attribution: str = ""


def node_ref_label(node: NodeRef) -> str:
    if node.surah is None or node.ayah_from is None:
        return f"{node.source}:{node.node_id}"
    span = str(node.ayah_from)
    if node.ayah_to is not None and node.ayah_to != node.ayah_from:
        span = f"{node.ayah_from}-{node.ayah_to}"
    return f"{node.source}:{node.surah}:{span}"


def evidence_from_nodes(nodes: list[NodeRef]) -> dict[str, Evidence]:
    items = [
        Evidence(f"e{i}", node_ref_label(n), n.source, n.text, attribution=n.source)
        for i, n in enumerate(nodes, start=1)
    ]
    return {e.id: e for e in items}


def evidence_from_web(passages: list, start: int = 1) -> dict[str, Evidence]:
    items = [
        Evidence(f"w{i}", p.url, "web", p.text, web=True, attribution=p.url)
        for i, p in enumerate(passages, start=start)
    ]
    return {e.id: e for e in items}


def check_draft(draft: object, retrieved: dict[str, Evidence]) -> str | None:
    """Return why the draft is unacceptable, or None. Never trusts the model's shape."""
    if not isinstance(draft, dict):
        return "المسودة ليست كائنًا"
    ids, lead_in = draft.get("citations"), draft.get("lead_in", "")
    if not isinstance(ids, list) or not ids or len(ids) > MAX_CITATIONS:
        return "عدد الاستشهادات غير صالح"
    if not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        return "معرّفات الاستشهاد غير صالحة"
    unknown = [i for i in ids if i not in retrieved]
    if unknown:
        return f"استشهاد بمعرّف لم يُسترجع: {', '.join(unknown)}"
    if not isinstance(lead_in, str) or len(lead_in) > MAX_LEAD_IN_CHARS:
        return "المقدمة غير صالحة"
    return None


def render(citation_ids: list[str], retrieved: dict[str, Evidence]) -> list[Citation]:
    """Citations whose text is copied from the evidence, byte for byte."""
    missing = [i for i in citation_ids if i not in retrieved]
    if missing:
        raise GroundingError(f"unknown citation ids: {missing}")
    return [
        Citation(ref=e.ref, source=e.source, text=e.text, web=e.web, attribution=e.attribution)
        for e in (retrieved[i] for i in citation_ids)
    ]


def compose_text(lead_in: str, citations: list[Citation]) -> str:
    body = "\n\n".join(f"{c.text} [{c.ref}]" for c in citations)
    lead = lead_in.strip()
    return f"{lead}\n\n{body}" if lead else body


def extractive_draft(retrieved: dict[str, Evidence]) -> dict:
    """The draft code writes itself: the fixed lead-in and the best-ranked passages."""
    return {"lead_in": DEFAULT_LEAD_IN, "citations": list(retrieved)[:KEPT_CITATIONS]}


def prompt_evidence(retrieved: dict[str, Evidence]) -> dict[str, Evidence]:
    """The passages shown to the writer (the best-ranked few); only these may be cited."""
    return dict(list(retrieved.items())[:PROMPT_PASSAGES])


def _plain(text: str) -> str:
    return " ".join(_MARKS.sub("", text).replace("ٱ", "ا").split())


def _is_arabic(text: str) -> bool:
    """Arabic only: the [ref] labels are ignored, any other Latin letter rejects the text."""
    prose = _LABEL.sub(" ", text)
    if _LATIN.search(prose):
        return False
    letters = _LETTER.findall(prose)
    return bool(letters) and len(_ARABIC_LETTER.findall(prose)) / len(letters) >= MIN_ARABIC_SHARE


def _quotes_verbatim(answer: str, shown: dict[str, Evidence]) -> bool:
    """Any text the model puts between ﴿ ﴾ must be a stretch of a retrieved passage."""
    pool = [_plain(e.text) for e in shown.values()]
    return all(any(_plain(q) in text for text in pool) for q in _QUOTE.findall(answer))


def _cited_sentences(raw: str, shown: dict[str, Evidence]) -> tuple[list[str], list[str]]:
    """Sentences that cite a shown passage (markers turned into ref labels) and the cited ids."""
    kept, ids = [], []
    for sentence in _SENTENCE_END.split(raw.strip()):
        found = [i for i in _MARKER.findall(sentence) if i in shown]
        if not found:
            continue  # a claim with no source is dropped, not trusted
        ids.extend(i for i in found if i not in ids)
        labelled = _MARKER.sub(lambda m: f"[{shown[m[1]].ref}]" if m[1] in shown else "", sentence)
        kept.append(" ".join(labelled.split()))
    return kept, ids


def parse_written(raw: object, shown: dict[str, Evidence]) -> dict | None:
    """Turn the model's free text into a draft, or None when it cannot be trusted.

    Kept: only sentences that cite a passage shown to the model, with [e1] replaced by the passage
    ref. Rejected: no cited sentence, mostly non-Arabic text, or a quoted verse that is not in the
    evidence. The cited passages are then inserted verbatim by `render`, never by the model.
    """
    if not isinstance(raw, str) or NO_INFO in raw:
        return None
    sentences, ids = _cited_sentences(raw, shown)
    answer = " ".join(sentences)
    if not ids or not _is_arabic(answer) or not _quotes_verbatim(answer, shown):
        return None
    return {"lead_in": answer, "citations": ids[:MAX_CITATIONS]}
