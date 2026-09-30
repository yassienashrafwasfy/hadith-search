import json
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import get_session, init_annotation_tables, now_iso
from models import Annotation, AnnotationProgress, Assignment, Hadith
from routers.auth import get_current_annotator
from services import overall_summary, summarize_query

router = APIRouter(prefix="/annotation", tags=["annotation"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

QUERIES_PATH = os.path.join(DATA_DIR, "queries.json")
QRELS_UNGRADED_PATH = os.path.join(DATA_DIR, "qrels_ungraded.json")


def load_json(path: str):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


_TEXT_COLUMNS = (
    Hadith.id,
    Hadith.Arabic_Text,
    Hadith.English_Text,
    Hadith.Book,
    Hadith.Normalized_Grade,
    Hadith.Hadith_Number,
    Hadith.Chapter_Number,
    Hadith.Chapter_Title_English,
    Hadith.Chapter_Title_Arabic,
)

_EMPTY_HADITH_TEXT = {
    "arabic_hadith": "",
    "english_hadith": "",
    "book": "",
    "normalized_grade": "",
    "reference": "",
    "in_book_reference": "",
}


def _hadith_text_entry(row) -> dict:
    return {
        "arabic_hadith": row.Arabic_Text or "",
        "english_hadith": row.English_Text or "",
        "book": row.Book or "",
        "normalized_grade": row.Normalized_Grade or "",
        "reference": str(row.Hadith_Number or ""),
        "in_book_reference": str(row.Chapter_Number or ""),
        "chapter_title_english": row.Chapter_Title_English or "",
        "chapter_title_arabic": row.Chapter_Title_Arabic or "",
    }


async def get_hadith_texts(hadith_ids: list[int]) -> dict[int, dict]:
    async with get_session() as session:
        result = await session.execute(select(*_TEXT_COLUMNS).where(Hadith.id.in_(hadith_ids)))
        rows = {row.id: row for row in result}
    return {
        hid: _hadith_text_entry(rows[hid]) if hid in rows else dict(_EMPTY_HADITH_TEXT)
        for hid in hadith_ids
    }


async def verify_assignment(annotator_id: int, query_id: str, session) -> bool:
    result = await session.execute(
        select(Assignment.id).where(
            Assignment.annotator_id == annotator_id, Assignment.query_id == query_id
        )
    )
    return result.first() is not None


async def get_annotator_labels(annotator_id: int, query_id: str, session) -> dict[str, int]:
    result = await session.execute(
        select(Annotation.hadith_id, Annotation.label).where(
            Annotation.annotator_id == annotator_id, Annotation.query_id == query_id
        )
    )
    return {str(row.hadith_id): row.label for row in result}


async def get_progress(annotator_id: int, query_id: str, session) -> int:
    result = await session.execute(
        select(AnnotationProgress.current_index).where(
            AnnotationProgress.annotator_id == annotator_id,
            AnnotationProgress.query_id == query_id,
        )
    )
    return result.scalar_one_or_none() or 0


async def set_progress(annotator_id: int, query_id: str, index: int, session):
    stmt = sqlite_insert(AnnotationProgress).values(
        annotator_id=annotator_id, query_id=query_id, current_index=index
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["annotator_id", "query_id"], set_={"current_index": index}
        )
    )


class LabelPayload(BaseModel):
    hadith_id: int
    index: int
    label: int


VALID_LABELS = (0, 1, 2)


async def get_db_session():
    """Request-scoped session (closed even when the handler raises)."""
    async with get_session() as session:
        yield session


async def _require_query(annotator: dict, query_id: str, session) -> str:
    """Query text for an assigned, existing query; 403 if unassigned, 404 if unknown."""
    if not await verify_assignment(annotator["id"], query_id, session):
        raise HTTPException(status_code=403, detail="You are not assigned to this query")
    queries_data = load_json(QUERIES_PATH)
    if query_id not in queries_data:
        raise HTTPException(status_code=404, detail="Query not found")
    return queries_data[query_id]


def _clamp_index(index: int, total: int) -> int:
    """Keep a saved progress index inside the pool (last item once everything is done)."""
    return min(index, total - 1) if index >= total and total else (0 if index >= total else index)


@router.get("/queries")
async def get_queries(
    annotator: dict = Depends(get_current_annotator), session=Depends(get_db_session)
):
    await init_annotation_tables()
    result = await session.execute(
        select(Assignment.query_id).where(Assignment.annotator_id == annotator["id"])
    )
    queries_data = load_json(QUERIES_PATH)
    qrels_ungraded = load_json(QRELS_UNGRADED_PATH)

    queries = []
    for qid in result.scalars().all():
        total = len(qrels_ungraded.get(qid, []))
        labels = await get_annotator_labels(annotator["id"], qid, session)
        progress = await get_progress(annotator["id"], qid, session)
        queries.append(
            {
                "query_id": qid,
                "query": queries_data.get(qid, ""),
                "total": total,
                "graded": len(labels),
                "current_index": _clamp_index(progress, total),
            }
        )
    return {"queries": queries}


@router.get("/{query_id}/current")
async def get_current_state(
    query_id: str,
    annotator: dict = Depends(get_current_annotator),
    session=Depends(get_db_session),
):
    await init_annotation_tables()
    query_text = await _require_query(annotator, query_id, session)
    pooled_ids = load_json(QRELS_UNGRADED_PATH).get(query_id, [])
    labels = await get_annotator_labels(annotator["id"], query_id, session)
    progress = await get_progress(annotator["id"], query_id, session)

    hadith_texts = await get_hadith_texts(pooled_ids)
    return {
        "query_id": query_id,
        "query": query_text,
        "current_index": _clamp_index(progress, len(pooled_ids)),
        "total": len(pooled_ids),
        "pooled_hadiths": [
            {"hadith_id": hid, **hadith_texts.get(hid, _EMPTY_HADITH_TEXT)} for hid in pooled_ids
        ],
        "labels": labels,
    }


async def _upsert_label(session, annotator_id: int, query_id: str, payload: LabelPayload) -> None:
    ts = now_iso()
    stmt = sqlite_insert(Annotation).values(
        annotator_id=annotator_id,
        query_id=query_id,
        hadith_id=payload.hadith_id,
        label=payload.label,
        created_at=ts,
        updated_at=ts,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["annotator_id", "query_id", "hadith_id"],
            set_={"label": payload.label, "updated_at": ts},
        )
    )


@router.post("/{query_id}/label")
async def save_label(
    query_id: str,
    payload: LabelPayload,
    annotator: dict = Depends(get_current_annotator),
    session=Depends(get_db_session),
):
    await init_annotation_tables()
    await _require_query(annotator, query_id, session)
    if payload.label not in VALID_LABELS:
        raise HTTPException(status_code=400, detail="Label must be 0, 1, or 2")

    pooled = load_json(QRELS_UNGRADED_PATH).get(query_id, [])
    await _upsert_label(session, annotator["id"], query_id, payload)

    next_index = payload.index + 1
    saved_index = next_index if next_index < len(pooled) else payload.index
    await set_progress(annotator["id"], query_id, saved_index, session)
    await session.commit()
    return {"success": True, "current_index": next_index}


@router.post("/{query_id}/navigate")
async def navigate(
    query_id: str,
    index: int,
    annotator: dict = Depends(get_current_annotator),
    session=Depends(get_db_session),
):
    await init_annotation_tables()
    await _require_query(annotator, query_id, session)
    pooled = load_json(QRELS_UNGRADED_PATH).get(query_id, [])
    if index < 0 or index >= len(pooled):
        raise HTTPException(status_code=400, detail="Invalid index")

    await set_progress(annotator["id"], query_id, index, session)
    await session.commit()
    return {"success": True, "current_index": index}


async def _labels_by_annotator(session, query_id: str) -> dict[int, dict[int, int]]:
    """{annotator_id: {hadith_id: label}} for everyone assigned to the query."""
    assigned = await session.execute(
        select(Assignment.annotator_id).where(Assignment.query_id == query_id)
    )
    labels = {}
    for annotator_id in assigned.scalars().all():
        result = await session.execute(
            select(Annotation.hadith_id, Annotation.label).where(
                Annotation.annotator_id == annotator_id, Annotation.query_id == query_id
            )
        )
        labels[annotator_id] = {row.hadith_id: row.label for row in result}
    return labels


@router.get("/stats/agreement")
async def get_agreement_stats(
    _annotator: dict = Depends(get_current_annotator), session=Depends(get_db_session)
):
    await init_annotation_tables()
    queries_data = load_json(QUERIES_PATH)
    qrels_ungraded = load_json(QRELS_UNGRADED_PATH)

    results = []
    for query_id, query_text in queries_data.items():
        pooled_ids = qrels_ungraded.get(query_id, [])
        if pooled_ids:
            labels = await _labels_by_annotator(session, query_id)
            results.append(summarize_query(query_id, query_text, pooled_ids, labels))

    return {
        "per_query": [r.entry for r in results],
        "overall": overall_summary(results, len(queries_data)),
    }
