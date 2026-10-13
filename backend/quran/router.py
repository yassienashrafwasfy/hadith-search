"""Source router: pick one source from the catalog for a question."""

import re

from quran.types import LLMClient, LLMError, SourceInfo

_MARKS = re.compile("[\u0640\u064b-\u065f\u0670]")
# Words that ask for an explanation, so a tafsir (not the bare verse text) should answer
TAFSIR_CUES = ("تفسير", "فسر", "معنى", "شرح", "اشرح", "المراد", "يقصد", "بيان", "سبب نزول")
PREFERRED_TAFSIR = "muyassar"  # short and clear: the default when no author is named


def _plain(text: str) -> str:
    return _MARKS.sub("", text)


def _author_source(question: str, sources: list[SourceInfo]) -> str | None:
    """The source whose author is named in the question, if any."""
    plain = _plain(question)
    for source in sources:
        for author in source.authors:
            name = _plain(author)
            name = name[2:] if name.startswith("ال") else name
            if name and name in plain:
                return source.name
    return None


def _rule_source(question: str, sources: list[SourceInfo]) -> str | None:
    """Deterministic routing the small controller gets wrong: an author, or text vs tafsir."""
    named = _author_source(question, sources)
    if named:
        return named
    verses = next((s.name for s in sources if not s.authors), None)
    if not any(cue in _plain(question) for cue in TAFSIR_CUES):
        return verses
    tafsirs = [s.name for s in sources if s.authors]
    preferred = next((n for n in tafsirs if PREFERRED_TAFSIR in n), None)
    return preferred or next(iter(tafsirs), None) or verses


def _prompt(question: str, sources: list[SourceInfo]) -> str:
    catalog = "\n".join(f"- {s.name}: {s.description}" for s in sources)
    return (
        "اختر المصدر الأنسب للسؤال من القائمة، أو null إن لم يناسب أي مصدر.\n"
        f"المصادر:\n{catalog}\nالسؤال: {question}"
    )


async def choose_source(llm: LLMClient, question: str, sources: list[SourceInfo]) -> str | None:
    """Rules first (author named, text vs tafsir), then the controller; None when nothing fits."""
    return _rule_source(question, sources) or await pick_with_llm(llm, question, sources)


async def pick_with_llm(llm: LLMClient, question: str, sources: list[SourceInfo]) -> str | None:
    """Ask the controller; None for an unknown name, no sources or an LLM failure."""
    if not sources:
        return None
    names = [s.name for s in sources]
    schema = {
        "type": "object",
        "properties": {"source": {"anyOf": [{"type": "string", "enum": names}, {"type": "null"}]}},
        "required": ["source"],
    }
    try:
        raw = await llm.generate_json(_prompt(question, sources), schema)
    except LLMError:
        return None
    name = raw.get("source") if isinstance(raw, dict) else None
    return name if isinstance(name, str) and name in names else None
