from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select, update

from database import get_session, now_iso
from models import KvPair
from rest import API_PREFIX, href, json_response, link, page_links

router = APIRouter(prefix=f"{API_PREFIX}/kv-pairs", tags=["kv-pairs"])

MAX_PAGE_SIZE = 200


class StatusUpdate(BaseModel):
    status: Literal["verified", "rejected"]


class BatchItem(StatusUpdate):
    id: int


def _pair_dict(pair: KvPair) -> dict:
    data = {
        "id": pair.id,
        "topic": pair.topic,
        "language": pair.language,
        "concept_en": pair.concept_en,
        "concept_ar": pair.concept_ar,
        "entity_en": pair.entity_en,
        "entity_ar": pair.entity_ar,
        "hadith_id": pair.hadith_id,
        "hadith_en": pair.hadith_en,
        "hadith_ar": pair.hadith_ar,
        "status": pair.status,
        "created_at": pair.created_at,
        "verified_at": pair.verified_at,
    }
    data["_links"] = {"self": link(href("kv-pairs", pair.id))}
    return data


@router.get("")
async def list_kv_pairs(
    request: Request,
    status: Optional[str] = None,
    topic: Optional[str] = None,
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
):
    conditions = []
    filters = {}
    if status:
        conditions.append(KvPair.status == status)
        filters["status"] = status
    if topic:
        conditions.append(KvPair.topic.like(f"%{topic}%"))
        filters["topic"] = topic

    async with get_session() as session:
        total = (
            await session.execute(select(func.count()).select_from(KvPair).where(*conditions))
        ).scalar_one()
        result = await session.execute(
            select(KvPair).where(*conditions).order_by(KvPair.id).limit(limit).offset(offset)
        )
        pairs = [_pair_dict(pair) for pair in result.scalars()]

    body = {
        "pairs": pairs,
        "total": total,
        "limit": limit,
        "offset": offset,
        "_links": page_links(href("kv-pairs"), filters, total, limit, offset),
    }
    return json_response(request, body)


@router.get("/statistics")
async def kv_pairs_statistics(request: Request):
    async with get_session() as session:
        total = (await session.execute(select(func.count()).select_from(KvPair))).scalar_one()
        by_status = dict(
            (
                await session.execute(select(KvPair.status, func.count()).group_by(KvPair.status))
            ).all()
        )
        by_topic = dict(
            (await session.execute(select(KvPair.topic, func.count()).group_by(KvPair.topic))).all()
        )

    body = {
        "total": total,
        "by_status": by_status,
        "by_topic": by_topic,
        "_links": {"self": link(href("kv-pairs", "statistics")), "pairs": link(href("kv-pairs"))},
    }
    return json_response(request, body)


@router.patch("/{pair_id}")
async def update_kv_pair(pair_id: int, update_request: StatusUpdate):
    async with get_session() as session:
        result = await session.execute(
            update(KvPair)
            .where(KvPair.id == pair_id)
            .values(status=update_request.status, verified_at=now_iso())
        )
        await session.commit()
        affected = result.rowcount

    if affected == 0:
        raise HTTPException(status_code=404, detail="KV pair not found")
    return {
        "id": pair_id,
        "status": update_request.status,
        "_links": {"self": link(href("kv-pairs", pair_id))},
    }


@router.patch("")
async def update_kv_pairs(items: list[BatchItem]):
    """Set the status of several pairs at once; ids that do not exist are skipped."""
    updated = 0
    async with get_session() as session:
        for item in items:
            result = await session.execute(
                update(KvPair)
                .where(KvPair.id == item.id)
                .values(status=item.status, verified_at=now_iso())
            )
            updated += result.rowcount
        await session.commit()
    return {"updated": updated}
