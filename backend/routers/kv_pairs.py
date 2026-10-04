from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from database import get_session, now_iso
from inputs import DbId, Text
from models import Hadith, KvPair
from rest import API_PREFIX, href, json_response, link, page_links
from routers.auth import get_current_annotator

# Every kv-pairs route reads or edits verification data, so all of them need a token.
router = APIRouter(
    prefix=f"{API_PREFIX}/kv-pairs",
    tags=["kv-pairs"],
    dependencies=[Depends(get_current_annotator)],
)

MAX_PAGE_SIZE = 200
MAX_OFFSET = 1_000_000  # the table holds a few thousand pairs; this only keeps absurd values out
MAX_BATCH = 100  # one UPDATE per item in one transaction, so the list must not be unbounded


class StatusUpdate(BaseModel):
    status: Literal["verified", "rejected"]


class BatchItem(StatusUpdate):
    id: DbId


def _pair_dict(pair: KvPair, hadith_en: str | None, hadith_ar: str | None) -> dict:
    data = {
        "id": pair.id,
        "topic": pair.topic,
        "language": pair.language,
        "concept_en": pair.concept_en,
        "concept_ar": pair.concept_ar,
        "entity_en": pair.entity_en,
        "entity_ar": pair.entity_ar,
        "hadith_id": pair.hadith_id,
        "hadith_en": hadith_en,
        "hadith_ar": hadith_ar,
        "status": pair.status,
        "created_at": pair.created_at,
        "verified_at": pair.verified_at,
    }
    data["_links"] = {"self": link(href("kv-pairs", pair.id))}
    return data


@router.get("")
async def list_kv_pairs(
    request: Request,
    status: Optional[Text] = None,
    topic: Optional[Text] = None,
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
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
        # The hadith text is read from `hadiths` (one copy of it), not stored on the pair.
        result = await session.execute(
            select(KvPair, Hadith.English_Text, Hadith.Arabic_Text)
            .join(Hadith, Hadith.id == KvPair.hadith_id)
            .where(*conditions)
            .order_by(KvPair.id)
            .limit(limit)
            .offset(offset)
        )
        pairs = [_pair_dict(pair, en, ar) for pair, en, ar in result]

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
async def update_kv_pair(pair_id: DbId, update_request: StatusUpdate):
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
async def update_kv_pairs(items: Annotated[list[BatchItem], Field(max_length=MAX_BATCH)]):
    """Set the status of several pairs at once; ids that do not exist are skipped."""
    updated = 0
    async with get_session() as session:
        # Ascending id order: two overlapping batches lock rows in the same order, so no deadlock.
        for item in sorted(items, key=lambda i: i.id):
            result = await session.execute(
                update(KvPair)
                .where(KvPair.id == item.id)
                .values(status=item.status, verified_at=now_iso())
            )
            updated += result.rowcount
        await session.commit()
    return {"updated": updated}
