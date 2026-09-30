import json
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import get_session, now_iso
from models import Annotation, AnnotationProgress, Assignment, Hadith
from rest import API_PREFIX, href, json_response, link
from routers.auth import get_current_annotator
from services import overall_summary, summarize_query

router = APIRouter(prefix=API_PREFIX, tags=["annotation"])

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
    stmt = pg_insert(AnnotationProgress).values(
        annotator_id=annotator_id, query_id=query_id, current_index=index
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["annotator_id", "query_id"], set_={"current_index": index}
        )
    )


class LabelPayload(BaseModel):
    label: Literal[0, 1, 2]


class ProgressPayload(BaseModel):
    index: int


async def get_db_session():
    """Request-scoped session (closed even when the handler raises)."""
    async with get_session() as session:
        yield session


async def _require_query(annotator: dict, query_id: str, session) -> str:
    """Query text for a query assigned to this annotator; 404 for anything else."""
    queries_data = load_json(QUERIES_PATH)
    if query_id not in queries_data or not await verify_assignment(
        annotator["id"], query_id, session
    ):
        raise HTTPException(status_code=404, detail="No such assignment")
    return queries_data[query_id]


def _clamp_index(index: int, total: int) -> int:
    """Keep a saved progress index inside the pool (last item once everything is done)."""
    return min(index, total - 1) if index >= total and total else (0 if index >= total else index)


def _assignment_links(query_id: str) -> dict:
    base = href("assignments", query_id)
    return {
        "self": link(base),
        "collection": link(href("assignments")),
        "progress": link(f"{base}/progress"),
        "label": link(f"{base}/labels/{{hadith_id}}", templated=True),
    }


@router.get("/assignments")
async def list_assignments(
    request: Request,
    annotator: dict = Depends(get_current_annotator),
    session=Depends(get_db_session),
):
    result = await session.execute(
        select(Assignment.query_id).where(Assignment.annotator_id == annotator["id"])
    )
    queries_data = load_json(QUERIES_PATH)
    qrels_ungraded = load_json(QRELS_UNGRADED_PATH)

    assignments = []
    for qid in result.scalars().all():
        total = len(qrels_ungraded.get(qid, []))
        labels = await get_annotator_labels(annotator["id"], qid, session)
        progress = await get_progress(annotator["id"], qid, session)
        assignments.append(
            {
                "query_id": qid,
                "query": queries_data.get(qid, ""),
                "total": total,
                "graded": len(labels),
                "current_index": _clamp_index(progress, total),
                "_links": {"self": link(href("assignments", qid))},
            }
        )
    body = {"assignments": assignments, "_links": {"self": link(href("assignments"))}}
    return json_response(request, body, private=True)


@router.get("/assignments/{query_id}")
async def get_assignment(
    query_id: str,
    request: Request,
    annotator: dict = Depends(get_current_annotator),
    session=Depends(get_db_session),
):
    query_text = await _require_query(annotator, query_id, session)
    pooled_ids = load_json(QRELS_UNGRADED_PATH).get(query_id, [])
    labels = await get_annotator_labels(annotator["id"], query_id, session)
    progress = await get_progress(annotator["id"], query_id, session)

    hadith_texts = await get_hadith_texts(pooled_ids)
    body = {
        "query_id": query_id,
        "query": query_text,
        "current_index": _clamp_index(progress, len(pooled_ids)),
        "total": len(pooled_ids),
        "pooled_hadiths": [
            {"hadith_id": hid, **hadith_texts.get(hid, _EMPTY_HADITH_TEXT)} for hid in pooled_ids
        ],
        "labels": labels,
        "_links": _assignment_links(query_id),
    }
    return json_response(request, body, private=True)


async def _upsert_label(session, annotator_id: int, query_id: str, hadith_id: int, label: int):
    """Returns True when the label is new (created), False when it replaced an earlier one."""
    existing = await session.execute(
        select(Annotation.label).where(
            Annotation.annotator_id == annotator_id,
            Annotation.query_id == query_id,
            Annotation.hadith_id == hadith_id,
        )
    )
    created = existing.first() is None
    ts = now_iso()
    stmt = pg_insert(Annotation).values(
        annotator_id=annotator_id,
        query_id=query_id,
        hadith_id=hadith_id,
        label=label,
        created_at=ts,
        updated_at=ts,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["annotator_id", "query_id", "hadith_id"],
            set_={"label": label, "updated_at": ts},
        )
    )
    return created


@router.put("/assignments/{query_id}/labels/{hadith_id}")
async def put_label(
    query_id: str,
    hadith_id: int,
    payload: LabelPayload,
    response: Response,
    annotator: dict = Depends(get_current_annotator),
    session=Depends(get_db_session),
):
    """Idempotent: 201 the first time a hadith is labelled, 200 when the label is replaced."""
    await _require_query(annotator, query_id, session)
    if hadith_id not in load_json(QRELS_UNGRADED_PATH).get(query_id, []):
        raise HTTPException(status_code=404, detail="Hadith is not in this query's pool")

    created = await _upsert_label(session, annotator["id"], query_id, hadith_id, payload.label)
    await session.commit()
    response.status_code = 201 if created else 200
    return {
        "query_id": query_id,
        "hadith_id": hadith_id,
        "label": payload.label,
        "_links": {
            "self": link(href("assignments", query_id, "labels", hadith_id)),
            "assignment": link(href("assignments", query_id)),
        },
    }


@router.put("/assignments/{query_id}/progress")
async def put_progress(
    query_id: str,
    payload: ProgressPayload,
    annotator: dict = Depends(get_current_annotator),
    session=Depends(get_db_session),
):
    await _require_query(annotator, query_id, session)
    pooled = load_json(QRELS_UNGRADED_PATH).get(query_id, [])
    if payload.index < 0 or payload.index >= len(pooled):
        raise HTTPException(status_code=422, detail="index is outside this query's pool")

    await set_progress(annotator["id"], query_id, payload.index, session)
    await session.commit()
    return {
        "index": payload.index,
        "_links": {
            "self": link(href("assignments", query_id, "progress")),
            "assignment": link(href("assignments", query_id)),
        },
    }


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


@router.get("/agreement")
async def get_agreement(
    request: Request,
    _annotator: dict = Depends(get_current_annotator),
    session=Depends(get_db_session),
):
    queries_data = load_json(QUERIES_PATH)
    qrels_ungraded = load_json(QRELS_UNGRADED_PATH)

    results = []
    for query_id, query_text in queries_data.items():
        pooled_ids = qrels_ungraded.get(query_id, [])
        if pooled_ids:
            labels = await _labels_by_annotator(session, query_id)
            results.append(summarize_query(query_id, query_text, pooled_ids, labels))

    body = {
        "per_query": [r.entry for r in results],
        "overall": overall_summary(results, len(queries_data)),
        "_links": {"self": link(href("agreement"))},
    }
    return json_response(request, body, private=True)
