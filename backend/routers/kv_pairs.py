from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select, update

from database import get_session, now_iso
from models import KvPair

router = APIRouter(prefix="/kv-pairs", tags=["kv-pairs"])


class VerifyRequest(BaseModel):
    status: str  # "verified" or "rejected"


def _pair_dict(pair: KvPair, *, full: bool = True) -> dict:
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
    }
    if full:
        data.update(status=pair.status, created_at=pair.created_at, verified_at=pair.verified_at)
    return data


@router.get("")
async def list_kv_pairs(
    status: Optional[str] = None,
    topic: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    conditions = []
    if status:
        conditions.append(KvPair.status == status)
    if topic:
        conditions.append(KvPair.topic.like(f"%{topic}%"))

    async with get_session() as session:
        total = (
            await session.execute(select(func.count()).select_from(KvPair).where(*conditions))
        ).scalar_one()
        result = await session.execute(
            select(KvPair).where(*conditions).order_by(KvPair.id).limit(limit).offset(offset)
        )
        pairs = [_pair_dict(pair) for pair in result.scalars()]

    return {"pairs": pairs, "total": total, "limit": limit, "offset": offset}


@router.get("/stats")
async def kv_pairs_stats():
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

    return {"total": total, "by_status": by_status, "by_topic": by_topic}


@router.post("/{pair_id}/verify")
async def verify_kv_pair(pair_id: int, req: VerifyRequest):
    if req.status not in ("verified", "rejected"):
        return {"error": "status must be 'verified' or 'rejected'"}

    async with get_session() as session:
        result = await session.execute(
            update(KvPair)
            .where(KvPair.id == pair_id)
            .values(status=req.status, verified_at=now_iso())
        )
        await session.commit()
        affected = result.rowcount

    if affected == 0:
        return {"error": "KV pair not found"}
    return {"id": pair_id, "status": req.status}


@router.post("/verify-batch")
async def verify_batch(reqs: list[dict]):
    updated = 0
    async with get_session() as session:
        for r in reqs:
            pair_id = r.get("id")
            status = r.get("status")
            if not pair_id or status not in ("verified", "rejected"):
                continue
            result = await session.execute(
                update(KvPair)
                .where(KvPair.id == pair_id)
                .values(status=status, verified_at=now_iso())
            )
            updated += result.rowcount
        await session.commit()
    return {"updated": updated}


@router.get("/export")
async def export_verified():
    async with get_session() as session:
        result = await session.execute(
            select(KvPair).where(KvPair.status == "verified").order_by(KvPair.id)
        )
        pairs = [_pair_dict(pair, full=False) for pair in result.scalars()]

    return {"pairs": pairs, "count": len(pairs)}
