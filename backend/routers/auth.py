import hashlib
import json
import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from database import get_session, init_annotation_tables, now_iso
from models import Annotator, Assignment, AuthSession

router = APIRouter(prefix="/auth", tags=["auth"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
QUERIES_PATH = os.path.join(DATA_DIR, "queries.json")

QUERIES_PER_ANNOTATOR = 2
ANNOTATORS_PER_QUERY = 3


class SignupRequest(BaseModel):
    username: str
    password: str


class SigninRequest(BaseModel):
    username: str
    password: str


def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return key.hex(), salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    key, _ = hash_password(password, salt)
    return secrets.compare_digest(key, stored_hash)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def load_queries() -> dict:
    with open(QUERIES_PATH, encoding="utf-8") as f:
        return json.load(f)


async def auto_assign_queries(annotator_id: int, session) -> list[str]:
    result = await session.execute(
        select(Assignment.query_id, func.count()).group_by(Assignment.query_id)
    )
    counts = dict(result.all())

    all_queries = load_queries()
    all_query_ids = list(all_queries.keys())
    sorted_queries = sorted(all_query_ids, key=lambda q: (counts.get(q, 0), q))

    assigned = []
    for qid in sorted_queries:
        if counts.get(qid, 0) < ANNOTATORS_PER_QUERY:
            session.add(Assignment(annotator_id=annotator_id, query_id=qid, assigned_at=now_iso()))
            assigned.append(qid)
        if len(assigned) >= QUERIES_PER_ANNOTATOR:
            break

    return assigned


async def get_current_annotator(authorization: str = Header(...)) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[7:]

    async with get_session() as session:
        result = await session.execute(
            select(AuthSession.annotator_id, Annotator.username)
            .join(Annotator, AuthSession.annotator_id == Annotator.id)
            .where(AuthSession.token == token)
        )
        row = result.first()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return {"id": row.annotator_id, "username": row.username}


async def get_annotator_assignments(annotator_id: int, session) -> list[str]:
    result = await session.execute(
        select(Assignment.query_id).where(Assignment.annotator_id == annotator_id)
    )
    return list(result.scalars())


def _assignment_details(query_ids: list[str]) -> list[dict]:
    all_queries = load_queries()
    return [{"query_id": qid, "query": all_queries.get(qid, "")} for qid in query_ids]


@router.post("/signup")
async def signup(req: SignupRequest):
    if len(req.username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    await init_annotation_tables()

    async with get_session() as session:
        existing = await session.execute(
            select(Annotator.id).where(Annotator.username == req.username)
        )
        if existing.first():
            raise HTTPException(status_code=409, detail="Username already taken")

        pw_hash, pw_salt = hash_password(req.password)
        annotator = Annotator(
            username=req.username,
            password_hash=pw_hash,
            password_salt=pw_salt,
            created_at=now_iso(),
        )
        session.add(annotator)
        await session.flush()
        annotator_id = annotator.id

        assigned = await auto_assign_queries(annotator_id, session)

        token = generate_token()
        session.add(AuthSession(token=token, annotator_id=annotator_id, created_at=now_iso()))
        await session.commit()

    return {
        "token": token,
        "annotator": {"id": annotator_id, "username": req.username},
        "assignments": _assignment_details(assigned),
    }


@router.post("/signin")
async def signin(req: SigninRequest):
    await init_annotation_tables()

    async with get_session() as session:
        result = await session.execute(select(Annotator).where(Annotator.username == req.username))
        annotator = result.scalar_one_or_none()

        if not annotator or not verify_password(
            req.password, annotator.password_hash, annotator.password_salt
        ):
            raise HTTPException(status_code=401, detail="Invalid username or password")

        token = generate_token()
        session.add(AuthSession(token=token, annotator_id=annotator.id, created_at=now_iso()))
        await session.commit()

        assigned_ids = await get_annotator_assignments(annotator.id, session)

    return {
        "token": token,
        "annotator": {"id": annotator.id, "username": annotator.username},
        "assignments": _assignment_details(assigned_ids),
    }


@router.post("/signout")
async def signout(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[7:]

    async with get_session() as session:
        await session.execute(delete(AuthSession).where(AuthSession.token == token))
        await session.commit()

    return {"success": True}


@router.get("/me")
async def me(annotator: dict = Depends(get_current_annotator)):
    async with get_session() as session:
        assigned_ids = await get_annotator_assignments(annotator["id"], session)

    return {
        "annotator": annotator,
        "assignments": _assignment_details(assigned_ids),
    }
