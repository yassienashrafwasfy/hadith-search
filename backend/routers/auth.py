import hashlib
import json
import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from database import get_session, init_annotation_tables, now_iso
from models import Annotator, Assignment
from ratelimit import auth_limit, limiter
from rest import API_PREFIX, href, json_response, link
from tokens import AuthSettings, auth_settings, issue_token, read_token

router = APIRouter(prefix=API_PREFIX, tags=["auth"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
QUERIES_PATH = os.path.join(DATA_DIR, "queries.json")

QUERIES_PER_ANNOTATOR = 2
ANNOTATORS_PER_QUERY = 3


# Upper bounds keep a huge body from turning every sign-in into an expensive hash.
class Credentials(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


class NewAnnotator(Credentials):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


# OWASP's floor for PBKDF2-HMAC-SHA256. Older rows used 100_000 and stored the bare salt;
# new rows store "<iterations>$<salt>" so the count can be raised later without a migration.
PBKDF2_ITERATIONS = 600_000
LEGACY_ITERATIONS = 100_000


def _split_salt(salt: str) -> tuple[int, str]:
    count, sep, raw = salt.partition("$")
    return (int(count), raw) if sep else (LEGACY_ITERATIONS, salt)


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Returns (hash hex, stored salt). Pass a stored salt back in to verify with its count."""
    if salt is None:
        salt = f"{PBKDF2_ITERATIONS}${secrets.token_hex(16)}"
    iterations, raw_salt = _split_salt(salt)
    key = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), raw_salt.encode("utf-8"), iterations
    )
    return key.hex(), salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    key, _ = hash_password(password, salt)
    return secrets.compare_digest(key, stored_hash)


# Hashed against when the username is unknown, so a miss takes as long as a wrong password
# and response time doesn't reveal which usernames exist.
_DUMMY_HASH, _DUMMY_SALT = hash_password("not-a-real-password")


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


async def get_current_annotator(
    authorization: str | None = Header(None), settings: AuthSettings = Depends(auth_settings)
) -> dict:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    annotator = read_token(token, settings)
    if annotator is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return annotator


async def get_annotator_assignments(annotator_id: int, session) -> list[str]:
    result = await session.execute(
        select(Assignment.query_id).where(Assignment.annotator_id == annotator_id)
    )
    return list(result.scalars())


def _assignment_details(query_ids: list[str]) -> list[dict]:
    all_queries = load_queries()
    return [
        {
            "query_id": qid,
            "query": all_queries.get(qid, ""),
            "_links": {"self": link(href("assignments", qid))},
        }
        for qid in query_ids
    ]


def _annotator_body(annotator: dict, assignment_ids: list[str]) -> dict:
    return {
        "id": annotator["id"],
        "username": annotator["username"],
        "assignments": _assignment_details(assignment_ids),
        "_links": {
            "self": link(href("annotators", annotator["id"])),
            "assignments": link(href("assignments")),
        },
    }


def _token_body(annotator: dict, assignment_ids: list[str], settings: AuthSettings) -> dict:
    return {
        "access_token": issue_token(annotator["id"], annotator["username"], settings),
        "token_type": "Bearer",
        "expires_in": settings.ttl_seconds,
        "annotator": _annotator_body(annotator, assignment_ids),
    }


@router.post("/annotators", status_code=201)
@limiter.limit(auth_limit)
async def create_annotator(
    request: Request,
    credentials: NewAnnotator,
    response: Response,
    settings: AuthSettings = Depends(auth_settings),
):
    """Sign up: creates the annotator, assigns queries and returns a first token."""
    await init_annotation_tables()

    async with get_session() as session:
        existing = await session.execute(
            select(Annotator.id).where(Annotator.username == credentials.username)
        )
        if existing.first():
            raise HTTPException(status_code=409, detail="Username already taken")

        pw_hash, pw_salt = hash_password(credentials.password)
        annotator = Annotator(
            username=credentials.username,
            password_hash=pw_hash,
            password_salt=pw_salt,
            created_at=now_iso(),
        )
        session.add(annotator)
        await session.flush()
        identity = {"id": annotator.id, "username": annotator.username}
        assigned = await auto_assign_queries(annotator.id, session)
        await session.commit()

    response.headers["Location"] = href("annotators", identity["id"])
    return _token_body(identity, assigned, settings)


@router.post("/tokens", status_code=201)
@limiter.limit(auth_limit)
async def create_token(
    request: Request,
    response: Response,
    credentials: Credentials,
    settings: AuthSettings = Depends(auth_settings),
):
    """Sign in: exchanges a username and password for a bearer token."""
    await init_annotation_tables()

    async with get_session() as session:
        result = await session.execute(
            select(Annotator).where(Annotator.username == credentials.username)
        )
        annotator = result.scalar_one_or_none()

        stored_hash = annotator.password_hash if annotator else _DUMMY_HASH
        stored_salt = annotator.password_salt if annotator else _DUMMY_SALT
        if not verify_password(credentials.password, stored_hash, stored_salt) or not annotator:
            raise HTTPException(status_code=401, detail="Invalid username or password")

        identity = {"id": annotator.id, "username": annotator.username}
        assigned_ids = await get_annotator_assignments(annotator.id, session)

    return _token_body(identity, assigned_ids, settings)


async def _annotator_response(request: Request, annotator: dict) -> Response:
    async with get_session() as session:
        assigned_ids = await get_annotator_assignments(annotator["id"], session)
    return json_response(request, _annotator_body(annotator, assigned_ids), private=True)


@router.get("/annotators/me")
async def get_current_annotator_resource(
    request: Request, annotator: dict = Depends(get_current_annotator)
):
    return await _annotator_response(request, annotator)


@router.get("/annotators/{annotator_id}")
async def get_annotator(
    annotator_id: int, request: Request, annotator: dict = Depends(get_current_annotator)
):
    if annotator_id != annotator["id"]:
        raise HTTPException(status_code=403, detail="You can only read your own profile")
    return await _annotator_response(request, annotator)
