from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request

from inputs import Text
from rest import API_PREFIX, href, json_response, link
from routers.search import get_search_context
from services.retrieval import SearchContext
from services.suggestions import MAX_SUGGESTIONS, suggest

CACHE_SECONDS = 300  # the vocabulary only changes when the index is rebuilt
MAX_QUERY_LENGTH = 100

router = APIRouter(prefix=API_PREFIX, tags=["search"])


@router.get("/suggestions")
def list_suggestions(
    request: Request,
    q: Annotated[Text, Query(min_length=1, max_length=MAX_QUERY_LENGTH)],
    limit: Annotated[int, Query(ge=1, le=MAX_SUGGESTIONS)] = MAX_SUGGESTIONS,
    ctx: SearchContext = Depends(get_search_context),
):
    """Autocomplete: vocabulary words and chapter titles that start with or look like `q`.

    Takes no search queue slot: one small indexed query, and at most `limit` (10) rows.
    """
    body = {
        "suggestions": suggest(ctx.session, q, limit),
        "_links": {
            "self": link(f"{href('suggestions')}?{urlencode({'q': q})}"),
            "searches": link(href("searches") + "{?q,method,lang}", templated=True),
        },
    }
    return json_response(request, body, max_age=CACHE_SECONDS)
