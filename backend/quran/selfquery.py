"""Self-query: turn a question into validated metadata filters. Invalid fields are dropped."""

from typing import Any

from pydantic import BaseModel

from quran.types import Filters, LLMClient, LLMError, Place, SourceInfo

MAX_SURAH = 114
MAX_JUZ = 30
MAX_AYAH = 286


class FilterDraft(BaseModel):
    """What the LLM may emit. Loose types on purpose: validation happens in `_clean_*`."""

    surah: Any = None
    ayah_from: Any = None
    ayah_to: Any = None
    juz: Any = None
    place: Any = None
    author: Any = None


def _int_in(value: Any, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if low <= value <= high else None


def _clean_place(value: Any) -> Place | None:
    try:
        return Place(value.strip().lower()) if isinstance(value, str) else None
    except ValueError:
        return None


def _clean_author(value: Any, source: SourceInfo) -> str | None:
    return value if isinstance(value, str) and value in source.authors else None


def _clean_range(draft: FilterDraft) -> tuple[int | None, int | None]:
    start = _int_in(draft.ayah_from, 1, MAX_AYAH)
    end = _int_in(draft.ayah_to, 1, MAX_AYAH)
    if start is not None and end is not None and start > end:
        return None, None
    return start, end


def validate_filters(draft: FilterDraft, source: SourceInfo) -> Filters:
    """Keep only values that pass their check and that the source can filter on."""
    start, end = _clean_range(draft)
    values = {
        "surah": _int_in(draft.surah, 1, MAX_SURAH),
        "ayah_from": start,
        "ayah_to": end,
        "juz": _int_in(draft.juz, 1, MAX_JUZ),
        "place": _clean_place(draft.place),
        "author": _clean_author(draft.author, source),
    }
    if source.fields:
        values = {k: v if k in source.fields else None for k, v in values.items()}
    if values["surah"] is None:  # an ayah range means nothing without its surah
        values["ayah_from"] = values["ayah_to"] = None
    return Filters(**values)


def _prompt(question: str, source: SourceInfo) -> str:
    authors = "، ".join(source.authors) or "-"
    return (
        "استخرج مرشحات البحث من السؤال. اترك الحقل null إن لم يُذكر صراحة.\n"
        "surah: رقم السورة 1-114. ayah_from, ayah_to: نطاق الآيات. juz: الجزء 1-30. "
        "place: meccan أو medinan. author: أحد المؤلفين التاليين فقط.\n"
        f"المؤلفون: {authors}\n"
        f"السؤال: {question}"
    )


async def extract_filters(llm: LLMClient, question: str, source: SourceInfo) -> Filters:
    """Ask the controller for filters; any LLM failure or malformed answer gives no filters."""
    try:
        raw = await llm.generate_json(_prompt(question, source), FilterDraft.model_json_schema())
        return validate_filters(FilterDraft.model_validate(raw), source)
    except (LLMError, ValueError, TypeError):
        return Filters()
