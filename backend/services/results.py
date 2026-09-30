"""Turn raw {hadith_id: score} rankings into API results."""

from models import HadithSchema, SearchResult

DEFAULT_TOP_K = 500


def _passes_filters(row, grade_filter: str | None, book_filter: str | None) -> bool:
    if grade_filter and row.get("Normalized_Grade") != grade_filter:
        return False
    if book_filter and row.get("Book") != book_filter:
        return False
    return True


def _to_hadith(hadith_id: int, row) -> HadithSchema:
    return HadithSchema(
        hadith_id=hadith_id,
        book=row.get("Book", ""),
        hadith_en_text=row.get("English_Text", ""),
        hadith_ar_text=row.get("Arabic_Text", ""),
        chapter_title_en=row.get("Chapter_Title_English", ""),
        chapter_title_ar=row.get("Chapter_Title_Arabic", ""),
        grade=row.get("Normalized_Grade", "Unknown"),
        raw_grade=row.get("Grade", "Unknown"),
        reference=row.get("Reference", ""),
        in_book_reference=row.get("In-book reference", ""),
    )


def build_results(
    raw: dict[int, float],
    hadiths_df,
    grade_filter: str | None = None,
    book_filter: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> list[SearchResult]:
    output: list[SearchResult] = []
    for hadith_id, score in raw.items():
        try:
            row = hadiths_df.loc[int(hadith_id)]
        except KeyError:
            continue
        if not _passes_filters(row, grade_filter, book_filter):
            continue
        output.append(SearchResult(hadith=_to_hadith(int(hadith_id), row), score=float(score)))
    return output[:top_k]
