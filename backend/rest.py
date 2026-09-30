"""HTTP conventions shared by every router: error bodies, cache headers and links.

Errors follow RFC 9457 (`application/problem+json`). Cacheable reads go through `json_response`,
which sets `Cache-Control` and a content-hash `ETag` and answers `If-None-Match` with 304, so
any server behind a load balancer produces the same validators for the same data.
"""

import hashlib
import json
from http import HTTPStatus
from urllib.parse import urlencode

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

API_PREFIX = "/api/v1"
PROBLEM_JSON = "application/problem+json"
EXPOSED_HEADERS = ["ETag", "Location", "Server-Timing", "WWW-Authenticate"]


def href(*parts: str) -> str:
    """Path under the API prefix: href("kv-pairs", 3) -> "/api/v1/kv-pairs/3"."""
    return "/".join([API_PREFIX, *(str(part).strip("/") for part in parts)])


def link(target: str, **attributes) -> dict:
    return {"href": target, **attributes}


def page_links(path: str, params: dict, total: int, limit: int, offset: int) -> dict:
    """self/first/prev/next/last links for an offset-paginated collection."""

    def at(page_offset: int) -> dict:
        query = urlencode({**params, "limit": limit, "offset": page_offset})
        return link(f"{path}?{query}")

    last_offset = max(0, (total - 1) // limit * limit) if limit else 0
    links = {"self": at(offset), "first": at(0), "last": at(last_offset)}
    if offset > 0:
        links["prev"] = at(max(0, offset - limit))
    if offset + limit < total:
        links["next"] = at(offset + limit)
    return links


def problem_response(
    status_code: int, detail: str, *, headers: dict | None = None, **extra
) -> JSONResponse:
    body = {"type": "about:blank", "title": HTTPStatus(status_code).phrase, "status": status_code}
    body.update(detail=detail, **extra)
    return JSONResponse(body, status_code=status_code, media_type=PROBLEM_JSON, headers=headers)


async def _http_exception_handler(_request: Request, exc: StarletteHTTPException):
    headers = dict(exc.headers or {})
    if exc.status_code == 401:
        headers.setdefault("WWW-Authenticate", "Bearer")
    return problem_response(exc.status_code, str(exc.detail), headers=headers)


async def _validation_handler(_request: Request, exc: RequestValidationError):
    errors = [
        {"field": ".".join(str(part) for part in error["loc"][1:]), "message": error["msg"]}
        for error in exc.errors()
    ]
    return problem_response(422, "Request validation failed", errors=errors)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)


def cache_control(max_age: int, private: bool) -> str:
    """`no-cache` means "store it, but revalidate with the ETag before reuse"."""
    scope = "private" if private else "public"
    return f"{scope}, max-age={max_age}" if max_age else f"{scope}, no-cache"


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    candidates = [value.strip().removeprefix("W/") for value in header.split(",")]
    return "*" in candidates or etag in candidates


class EncodedJson(str):
    """A body that is already JSON text, so `json_response` does not encode it a second time."""


def json_response(
    request: Request,
    body,
    *,
    max_age: int = 0,
    private: bool = False,
    headers: dict | None = None,
) -> Response:
    """200 with validators, or an empty 304 when the client's ETag is still current."""
    if isinstance(body, EncodedJson):
        payload = str(body)
    else:
        payload = json.dumps(jsonable_encoder(body), ensure_ascii=False, separators=(",", ":"))
    etag = f'"{hashlib.sha256(payload.encode()).hexdigest()[:32]}"'
    response_headers = {
        "ETag": etag,
        "Cache-Control": cache_control(max_age, private),
        **(headers or {}),
    }
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=response_headers)
    return Response(payload, media_type="application/json", headers=response_headers)


def server_timing(name: str, milliseconds: float) -> str:
    return f"{name};dur={milliseconds:.1f}"
