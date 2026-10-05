"""SQLAlchemy ORM models (PostgreSQL): corpus, search index, annotation platform, KV pairs."""

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    and_,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _text() -> Mapped[str | None]:
    return mapped_column(Text, nullable=True)


def _int() -> Mapped[int | None]:
    return mapped_column(Integer, nullable=True)


class Book(Base):
    """One of the six collections. `lk_book` is the short name used by the LK corpus files."""

    __tablename__ = "books"

    book: Mapped[str] = mapped_column(Text, primary_key=True)
    lk_book: Mapped[str | None] = mapped_column(Text, unique=True)


class Chapter(Base):
    """A chapter is identified by its book and number; the titles depend on nothing else.

    Sections are not a table: (Book, Section_Number) does not fix the section titles, and a
    chapter can span several sections, so the section columns stay on `hadiths`.
    """

    __tablename__ = "chapters"
    __table_args__ = (
        Index(
            "ix_chapters_title_english_trgm",
            "title_english",
            postgresql_using="gin",
            postgresql_ops={"title_english": "gin_trgm_ops"},
        ),
        Index(
            "ix_chapters_title_arabic_trgm",
            "title_arabic",
            postgresql_using="gin",
            postgresql_ops={"title_arabic": "gin_trgm_ops"},
        ),
    )

    book: Mapped[str] = mapped_column(ForeignKey("books.book"), primary_key=True)
    chapter_number: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    title_english = _text()
    title_arabic = _text()


class Hadith(Base):
    """Bilingual corpus row. Chapter and book facts live in `chapters` and `books`,
    the preprocessed texts in `hadith_preprocessed`; `HADITH_CHAPTER` joins the first."""

    __tablename__ = "hadiths"
    __table_args__ = (
        ForeignKeyConstraint(
            ["Book", "Chapter_Number"],
            ["chapters.book", "chapters.chapter_number"],
            name="fk_hadiths_chapter",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    Book: Mapped[str | None] = mapped_column(Text, ForeignKey("books.book"), index=True)
    Source_File = _text()

    Chapter_Number = _int()
    # Text: the corpus has ranges such as "622 -623" and "5, 6" that an integer column rejects
    Section_Number = _text()
    Section_English = _text()
    Section_Arabic = _text()
    Hadith_Number = _text()

    English_Isnad = _text()
    English_Text = _text()
    English_Matn = _text()
    English_Text_Source = _text()
    English_Isnad_Source = _text()
    English_Matn_Source = _text()

    Arabic_Isnad = _text()
    Arabic_Text = _text()
    Arabic_Matn = _text()
    Arabic_Text_Source = _text()
    Arabic_Isnad_Source = _text()
    Arabic_Matn_Source = _text()
    Arabic_Comment = _text()

    English_Grade = _text()
    Arabic_Grade = _text()
    Normalized_Grade: Mapped[str | None] = mapped_column(Text, index=True)

    Has_English_Content = _int()
    Has_Arabic_Content = _int()
    Has_English_Matn = _int()
    Has_Arabic_Matn = _int()


# The join condition from a hadith to its chapter: `.outerjoin(Chapter, HADITH_CHAPTER)`.
HADITH_CHAPTER = and_(Chapter.book == Hadith.Book, Chapter.chapter_number == Hadith.Chapter_Number)


class HadithPreprocessed(Base):
    """The six preprocessed texts of a hadith (input to the BM25 build, rebuilt by preprocess.py)."""

    __tablename__ = "hadith_preprocessed"

    hadith_id: Mapped[int] = mapped_column(
        ForeignKey("hadiths.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    Preprocessed_English = _text()
    Preprocessed_Arabic = _text()
    Preprocessed_English_Isnad = _text()
    Preprocessed_Arabic_Isnad = _text()
    Preprocessed_English_Matn = _text()
    Preprocessed_Arabic_Matn = _text()


class HadithExactText(Base):
    """The text exact search matches on, one row per hadith (rebuilt with the index, see
    `services.exact_text`). Lowercase, Arabic marks removed, every run of non-letters turned into
    one space and a space at both ends, so a whole word is the substring ` word ` and a trigram
    index can serve it. `ix_*_trgm` need the pg_trgm extension (`database.init_schema`)."""

    __tablename__ = "hadith_exact_text"
    __table_args__ = (
        Index(
            "ix_hadith_exact_text_english_trgm",
            "english",
            postgresql_using="gin",
            postgresql_ops={"english": "gin_trgm_ops"},
        ),
        Index(
            "ix_hadith_exact_text_arabic_trgm",
            "arabic",
            postgresql_using="gin",
            postgresql_ops={"arabic": "gin_trgm_ops"},
        ),
    )

    hadith_id: Mapped[int] = mapped_column(
        ForeignKey("hadiths.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    english: Mapped[str] = mapped_column(Text, server_default="  ")
    arabic: Mapped[str] = mapped_column(Text, server_default="  ")


class HadithEmbedding(Base):
    """One Arabic sentence vector per hadith. No fixed dimension, so a different model needs no migration."""

    __tablename__ = "hadith_embeddings"

    hadith_id: Mapped[int] = mapped_column(
        ForeignKey("hadiths.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    arabic = mapped_column(VECTOR(), nullable=True)


class EmbeddingSet(Base):
    """One re-embedded copy of the corpus made by `scripts/promote_model.py`.

    The vectors live in their own table, `hadith_embeddings_<release>` (see `models/embedding_sets.py`),
    so a new model never touches the vectors the live colour reads. This row says which registry
    version made them. The default table, `hadith_embeddings`, has no row here.
    """

    __tablename__ = "embedding_sets"

    release: Mapped[str] = mapped_column(Text, primary_key=True)
    model_version: Mapped[str] = mapped_column(Text)
    dim: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text)


class HadithLength(Base):
    """Token counts of the preprocessed matn, the document lengths BM25 normalises by."""

    __tablename__ = "hadith_lengths"

    hadith_id: Mapped[int] = mapped_column(
        ForeignKey("hadiths.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    english_len: Mapped[int] = mapped_column(Integer)
    arabic_len: Mapped[int] = mapped_column(Integer)


class Term(Base):
    """A term of the inverted index with its document frequency. `language` is "EN" or "AR"."""

    __tablename__ = "terms"
    __table_args__ = (
        Index(
            "ix_terms_term_trgm",
            "term",
            postgresql_using="gin",
            postgresql_ops={"term": "gin_trgm_ops"},
        ),
    )

    language: Mapped[str] = mapped_column(Text, primary_key=True)
    term: Mapped[str] = mapped_column(Text, primary_key=True)
    df: Mapped[int] = mapped_column(Integer)


class Posting(Base):
    """Inverted index row: `term` occurs `tf` times in the matn of `hadith_id`."""

    __tablename__ = "postings"
    __table_args__ = (Index("ix_postings_hadith_id", "hadith_id"),)

    language: Mapped[str] = mapped_column(Text, primary_key=True)
    term: Mapped[str] = mapped_column(Text, primary_key=True)
    hadith_id: Mapped[int] = mapped_column(
        ForeignKey("hadiths.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    tf: Mapped[int] = mapped_column(Integer)


class Annotator(Base):
    __tablename__ = "annotators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    password_salt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("annotator_id", "query_id"),
        Index("ix_assignments_query_id", "query_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    annotator_id: Mapped[int] = mapped_column(ForeignKey("annotators.id", ondelete="CASCADE"))
    query_id: Mapped[str] = mapped_column(Text)
    assigned_at: Mapped[str] = mapped_column(Text)


class Annotation(Base):
    __tablename__ = "annotations"
    __table_args__ = (
        CheckConstraint("label IN (0, 1, 2)", name="ck_annotations_label"),
        Index("ix_annotations_hadith_id", "hadith_id"),
    )

    annotator_id: Mapped[int] = mapped_column(
        ForeignKey("annotators.id", ondelete="CASCADE"), primary_key=True
    )
    query_id: Mapped[str] = mapped_column(Text, primary_key=True)
    hadith_id: Mapped[int] = mapped_column(
        ForeignKey("hadiths.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    label: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class AnnotationProgress(Base):
    __tablename__ = "annotation_progress"
    __table_args__ = (
        CheckConstraint("current_index >= 0", name="ck_annotation_progress_current_index"),
    )

    annotator_id: Mapped[int] = mapped_column(
        ForeignKey("annotators.id", ondelete="CASCADE"), primary_key=True
    )
    query_id: Mapped[str] = mapped_column(Text, primary_key=True)
    current_index: Mapped[int | None] = mapped_column(Integer, default=0)


class KvPair(Base):
    __tablename__ = "kv_pairs"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'verified', 'rejected')", name="ck_kv_pairs_status"),
        Index("idx_kv_pairs_status", "status"),
        Index("idx_kv_pairs_topic", "topic"),
        Index("idx_kv_pairs_hadith_id", "hadith_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text)
    concept_en: Mapped[str] = mapped_column(Text)
    concept_ar: Mapped[str] = mapped_column(Text)
    entity_en: Mapped[str] = mapped_column(Text)
    entity_ar: Mapped[str] = mapped_column(Text)
    hadith_id: Mapped[int] = mapped_column(ForeignKey("hadiths.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    created_at: Mapped[str] = mapped_column(Text)
    verified_at: Mapped[str | None] = mapped_column(Text)
