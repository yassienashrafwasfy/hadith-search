from fastapi import APIRouter, HTTPException, Request

from database import get_hadith_row
from rest import API_PREFIX, href, json_response, link

router = APIRouter(prefix=f"{API_PREFIX}/hadiths", tags=["hadiths"])

CACHE_SECONDS = 3600  # the corpus only changes when the build pipeline is re-run


@router.get("/{hadith_id}")
def get_hadith(hadith_id: int, request: Request):
    row = get_hadith_row(hadith_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Hadith not found")
    body = {**row, "_links": {"self": link(href("hadiths", hadith_id))}}
    return json_response(request, body, max_age=CACHE_SECONDS)
