"""Offline summaries of passages and surahs. Used for routing and coarse retrieval only; the
original text is never replaced by a summary."""

import asyncio
from collections.abc import Sequence

from quran.types import LLMClient, LLMError

MAX_INPUT_CHARS = 6000
MAX_SUMMARY_TOKENS = 200

PASSAGE_PROMPT = (
    "لخّص المقطع التالي من {kind} في جملتين أو ثلاث بالعربية الفصحى، "
    "دون إضافة معلومة غير واردة فيه.\n\n{text}\n\nالملخص:"
)
KINDS = {"passage": "القرآن الكريم أو تفسيره", "surah": "سورة، وهو مكوّن من ملخصات مقاطعها"}


def build_prompt(text: str, level: str) -> str:
    return PASSAGE_PROMPT.format(kind=KINDS[level], text=text[:MAX_INPUT_CHARS])


async def summarise(llm: LLMClient, text: str, level: str = "passage") -> str | None:
    """One summary, or None when the LLM fails or answers with nothing (the node stays unsummarised)."""
    try:
        answer = await llm.generate_text(build_prompt(text, level), MAX_SUMMARY_TOKENS)
    except LLMError:
        return None
    return answer.strip() or None


async def summarise_many(
    llm: LLMClient, texts: Sequence[str], level: str = "passage", concurrency: int = 1
) -> list[str | None]:
    """Summaries in input order. `concurrency` 1 suits a single CPU-bound llama-server."""
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(text: str) -> str | None:
        async with gate:
            return await summarise(llm, text, level)

    return list(await asyncio.gather(*(one(t) for t in texts)))
