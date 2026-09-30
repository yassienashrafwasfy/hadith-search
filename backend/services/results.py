"""Turn raw {hadith_id: score} rankings into API results."""

from sqlalchemy import ARRAY, Integer, any_, bindparam, select
from sqlalchemy.orm import Session

from models import Hadith, HadithSchema, SearchResult

DEFAULT_TOP_K = 500


_COLUMNS = (
    Hadith.id,
    Hadith.Book,
    Hadith.English_Text,
    Hadith.Arabic_Text,
    Hadith.Chapter_Title_English,
    Hadith.Chapter_Title_Arabic,
    Hadith.Normalized_Grade,
    Hadith.Grade,
)


def _in(ids: list[int]):
    """`id = ANY(array)`: one parameter however many ids, instead of one placeholder per id."""
    return Hadith.id == any_(bindparam("ids", ids, type_=ARRAY(Integer)))


def _to_hadith(row) -> HadithSchema:
    # model_construct skips validation: the values come from the database, not from a client.
    return HadithSchema.model_construct(
        hadith_id=row.id,
        book=row.Book or "",
        hadith_en_text=row.English_Text or "",
        hadith_ar_text=row.Arabic_Text or "",
        chapter_title_en=row.Chapter_Title_English or "",
        chapter_title_ar=row.Chapter_Title_Arabic or "",
        grade=row.Normalized_Grade or "Unknown",
        raw_grade=row.Grade or "Unknown",
        reference="",  # the corpus has no reference columns
        in_book_reference="",
    )


def build_results(
    session: Session,
    raw: dict[int, float],
    grade_filter: str | None = None,
    book_filter: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> list[SearchResult]:
    """Rows for the ranked ids (best first), filtered by grade/book, cut to `top_k`.

    A keyword search can match thousands of hadiths. Only the ids are checked against the
    filters; the texts are then loaded for the `top_k` that stay, and only the columns the
    response uses (not the preprocessed texts).
    """
    ids = list(raw)
    if grade_filter or book_filter:
        stmt = select(Hadith.id).where(_in(ids))
        if grade_filter:
            stmt = stmt.where(Hadith.Normalized_Grade == grade_filter)
        if book_filter:
            stmt = stmt.where(Hadith.Book == book_filter)
        allowed = set(session.scalars(stmt))
        ids = [hadith_id for hadith_id in ids if hadith_id in allowed]
    ids = ids[:top_k]
    rows = {row.id: row for row in session.execute(select(*_COLUMNS).where(_in(ids)))}
    return [
        SearchResult.model_construct(
            hadith=_to_hadith(rows[hadith_id]), score=float(raw[hadith_id])
        )
        for hadith_id in ids
        if hadith_id in rows
    ]
