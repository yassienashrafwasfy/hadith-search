"""Turn raw {hadith_id: score} rankings into API results."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Hadith, HadithSchema, SearchResult

DEFAULT_TOP_K = 500


def _to_hadith(row: Hadith) -> HadithSchema:
    return HadithSchema(
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
    """Rows for the ranked ids (best first), filtered by grade/book, cut to `top_k`."""
    stmt = select(Hadith).where(Hadith.id.in_(list(raw)))
    if grade_filter:
        stmt = stmt.where(Hadith.Normalized_Grade == grade_filter)
    if book_filter:
        stmt = stmt.where(Hadith.Book == book_filter)
    rows = {row.id: row for row in session.scalars(stmt)}
    return [
        SearchResult(hadith=_to_hadith(rows[hadith_id]), score=float(score))
        for hadith_id, score in raw.items()
        if hadith_id in rows
    ][:top_k]
