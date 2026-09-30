"""SQLAlchemy ORM models for hadiths.db (corpus + annotation platform + KV pairs)."""

from sqlalchemy import ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _text() -> Mapped[str | None]:
    return mapped_column(Text, nullable=True)


def _int() -> Mapped[int | None]:
    return mapped_column(Integer, nullable=True)


class Hadith(Base):
    """Bilingual corpus row. Column names mirror the LK corpus build in data_creation.py."""

    __tablename__ = "hadiths"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    Book: Mapped[str | None] = mapped_column(Text, index=True)
    LK_Book = _text()
    Source_File = _text()

    Chapter_Number = _int()
    Chapter_Title_English = _text()
    Chapter_Title_Arabic = _text()
    Chapter_English = _text()
    Chapter_Arabic = _text()
    Section_Number = _int()
    Section_English = _text()
    Section_Arabic = _text()
    Hadith_Number = _int()

    English_Hadith = _text()
    English_Text = _text()
    English_Isnad = _text()
    English_Matn = _text()
    English_Text_Source = _text()
    English_Isnad_Source = _text()
    English_Matn_Source = _text()

    Arabic_Hadith = _text()
    Arabic_Text = _text()
    Arabic_Isnad = _text()
    Arabic_Matn = _text()
    Arabic_Text_Source = _text()
    Arabic_Isnad_Source = _text()
    Arabic_Matn_Source = _text()
    Arabic_Comment = _text()

    English_Grade = _text()
    Arabic_Grade = _text()
    Grade = _text()
    Normalized_Grade: Mapped[str | None] = mapped_column(Text, index=True)

    Has_English_Content = _int()
    Has_Arabic_Content = _int()
    Has_English_Matn = _int()
    Has_Arabic_Matn = _int()

    Preprocessed_English = _text()
    Preprocessed_Arabic = _text()
    Preprocessed_English_Isnad = _text()
    Preprocessed_Arabic_Isnad = _text()
    Preprocessed_English_Matn = _text()
    Preprocessed_Arabic_Matn = _text()


class Annotator(Base):
    __tablename__ = "annotators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    password_salt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("annotator_id", "query_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    annotator_id: Mapped[int] = mapped_column(ForeignKey("annotators.id", ondelete="CASCADE"))
    query_id: Mapped[str] = mapped_column(Text)
    assigned_at: Mapped[str] = mapped_column(Text)


class Annotation(Base):
    __tablename__ = "annotations"

    annotator_id: Mapped[int] = mapped_column(
        ForeignKey("annotators.id", ondelete="CASCADE"), primary_key=True
    )
    query_id: Mapped[str] = mapped_column(Text, primary_key=True)
    hadith_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    label: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class AnnotationProgress(Base):
    __tablename__ = "annotation_progress"

    annotator_id: Mapped[int] = mapped_column(
        ForeignKey("annotators.id", ondelete="CASCADE"), primary_key=True
    )
    query_id: Mapped[str] = mapped_column(Text, primary_key=True)
    current_index: Mapped[int | None] = mapped_column(Integer, default=0)


class KvPair(Base):
    __tablename__ = "kv_pairs"
    __table_args__ = (
        Index("idx_kv_pairs_status", "status"),
        Index("idx_kv_pairs_topic", "topic"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text)
    concept_en: Mapped[str] = mapped_column(Text)
    concept_ar: Mapped[str] = mapped_column(Text)
    entity_en: Mapped[str] = mapped_column(Text)
    entity_ar: Mapped[str] = mapped_column(Text)
    hadith_id: Mapped[int] = mapped_column(Integer)
    hadith_en: Mapped[str | None] = mapped_column(Text)
    hadith_ar: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    created_at: Mapped[str] = mapped_column(Text)
    verified_at: Mapped[str | None] = mapped_column(Text)
