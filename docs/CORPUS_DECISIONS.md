# Corpus Construction Decisions

> This document records every data filtering, dropping, and transformation decision made during corpus construction, with the rationale at a level suitable for inclusion in the Methodology section of a research paper. Decisions are recorded in chronological order.

---

## Source Corpus

**Name**: LK Hadith Corpus  
**Repository**: https://github.com/ShathaTm/LK-Hadith-Corpus  
**Format**: CSV files, one directory per book  
**Collections covered**: Six canonical Sunni hadith collections

| Book key | Full name |
|----------|-----------|
| Bukhari | Sahih al-Bukhari |
| Muslim | Sahih Muslim |
| AbuDaud | Sunan Abi Dawud |
| Nesai | Sunan an-Nasa'i |
| IbnMaja | Sunan Ibn Majah |
| Tirmizi | Jami` at-Tirmidhi |

The LK corpus provides, for each hadith: the full text in English and Arabic, the isnad (narrator chain) in both languages, the matn (hadith body) in both languages, chapter and section metadata, and grading information from both English and Arabic scholarly sources.

---

## Decision 1: Matn-Focused Retrieval Field

**Date**: Phase 9 (July 2026)  
**Decision**: Index and embed the **matn** (hadith body text) only, excluding the isnad (narrator chain) from the retrieval field.

**Rationale**: The isnad encodes transmission provenance — chains of narrators by name — rather than topical content. A query such as "rules of giving charity" should retrieve hadiths whose body discusses charity, not hadiths narrated by scholars whose names happen to match query terms. Sparse and dense retrieval use matn only; chapter titles and isnad remain stored as metadata.

**Implementation**: `backend/scripts/build_inverted_index.py` indexes `Preprocessed_English_Matn` and `Preprocessed_Arabic_Matn` only. Dense building, training, and fine-tuned re-encoding use `passage: {Matn}` only. Arabic passages use the same light normalization and diacritic removal as Arabic dense queries.

---

## Decision 2: Deterministic Matn/Isnad Reconstruction

**Date**: Phase 9 (July 2026)  
**Decision**: When a hadith record has two of the three fields {full text, isnad, matn}, derive the missing third field deterministically before any filtering step.

**Rationale**: The LK corpus does not uniformly provide all three fields for all hadiths. Many records have the full text and the isnad but not a separately extracted matn, or have the full text and the matn but not the isnad. Discarding these records without attempting reconstruction would artificially reduce corpus size. Reconstruction is deterministic (no model inference, no ambiguity) and therefore fully reproducible.

**Rules applied** (in order, per language, per row):

1. If `Text` is missing but both `Isnad` and `Matn` are present: `Text = Isnad + " " + Matn`
2. If `Matn` is missing but both `Text` and `Isnad` are present: `Matn = Text[len(Isnad):]` (strip isnad prefix from full text)
3. If `Isnad` is missing but both `Text` and `Matn` are present: `Isnad = Text[:-len(Matn)]` (strip matn suffix from full text)

**Results**: Reconstruction recovered **450 Arabic matn fields** in the validation run.

**Implementation**: `backend/scripts/data_creation.py`, function `apply_deterministic_reconstruction()`.

---

## Decision 3: Drop Rows Missing Bilingual Matn (First-Stage Drop)

**Date**: Phase 9 (July 2026)  
**Decision**: After reconstruction, drop any row where either `English_Matn` or `Arabic_Matn` remains absent or empty.

**Rationale**: This system is designed for bilingual retrieval — users can submit queries in either English or Arabic and retrieve across both. A hadith that has content in only one language is unindexable in the other language's retrieval path, creating an asymmetry where English and Arabic indices cover different document sets. To maintain a consistent, symmetric retrieval corpus, both language fields must be present.

**Result**: **597 rows** dropped. Audit written to `backend/data/dropped_lk_rows.json`. Canonical corpus size after this step: **33,491 rows**.

**Breakdown by drop reason**:
- Missing `English_Matn` only
- Missing `Arabic_Matn` only
- Missing both

(Exact per-reason counts are in `dropped_lk_rows.json`.)

**Implementation**: `backend/scripts/data_creation.py`, function `drop_rows_missing_bilingual_matn()`.

---

## Decision 4: Drop Rows Where Matn Preprocessing Collapses to Empty (Second-Stage Drop)

**Date**: Phase 9 (July 2026) — pending implementation  
**Decision**: After preprocessing, drop any row where the preprocessed matn collapses to an empty string in either language.

**Rationale**: A non-empty raw matn field is a necessary but not sufficient condition for indexability. The preprocessing pipeline applies tokenization, lemmatization, diacritics removal, and stopword filtering. For some rows, the raw matn field contains only content that is entirely consumed by this pipeline, yielding no indexable tokens. Two categories of such rows were identified:

**Category A — Arabic punctuation-only matn (410 rows)**:
These rows have Arabic matn fields containing only directional marks and punctuation, such as `\u200f.\u200f` (RIGHT-TO-LEFT MARK + period). This occurs because the LK corpus uses this pattern as a stand-in for "same text as the preceding hadith" — i.e., these are alternate isnad chains that point to a shared matn recorded above them. The raw matn is not extractable from the full text either: querying the database confirms that for every one of these rows, `TRIM(Arabic_Text) ≈ TRIM(Arabic_Isnad)`, meaning the full text is itself purely the narrator chain with no substantive body content. Falling back to the full text would yield only isnad tokens, which is precisely the noise source that Decision 1 was designed to eliminate.

**Category B — English cross-reference matn (17 rows)**:
These rows have English matn fields containing only cross-reference phrases such as `"As above."`, `"See hadith 4909."`, or `"Same as above."`. After preprocessing, these collapse to empty strings. The full text for these rows is equivalent (e.g., `"Narrated Abu Is-haq: As above."`) and also collapses to empty or near-empty after preprocessing. There is no recoverable content.

**Verification**: A direct SQL query confirmed **zero rows** exist where the matn is empty but the full text differs substantively from the isnad. The full-text fallback would not rescue any of these rows.

**Symmetry requirement**: Since English and Arabic indices must cover the same document set (per the rationale in Decision 3), a row that is unindexable in either language must be dropped from both. This is a **two-stage drop policy**: the first stage (Decision 3) catches structurally missing matn before preprocessing; the second stage (this decision) catches matn that is structurally present but semantically empty after preprocessing.

**Result**: Approximately **427 additional rows** to be dropped (410 Arabic + 17 English; overlap between categories is not confirmed as zero, so final count may differ slightly). These rows will be recorded in the existing `dropped_lk_rows.json` audit with an extended reason field.

**Expected canonical corpus size after this step**: approximately **33,064 rows** (exact count pending pipeline rerun).

**Implementation**: `backend/scripts/preprocess.py` — remove hard `ValueError`, replace with detection and drop. `backend/scripts/data_creation.py` — optionally extend drop audit. Alternatively, the drop is enforced at build time by excluding empty-preprocessed rows from index and embedding construction.

---

## Decision 5: Grade Normalization

**Date**: Phase 9 (July 2026)  
**Decision**: Normalize hadith grades from heterogeneous English and Arabic grade strings into five canonical categories: `Sahih`, `Hasan`, `Da'if`, `Mawdu`, and `Unknown`.

**Rationale**: The LK corpus stores grades as free-form strings from multiple scholarly sources, with variations in spelling, language, and granularity (e.g., `"Sahih"`, `"Sahih li ghayrihi"`, `"Hasan Sahih"`, `"Da'if"`, `"Mawdu'"`, `"Fabricated"`). Normalization is required for grade-stratified analysis in the paper. The normalization applies priority rules when English and Arabic grades conflict.

**Priority rules**:
1. If both grades agree on a single category, use that category.
2. `Hasan Sahih` (a combined grade in classical hadith science) → `Hasan`
3. Conflicting grades → `Unknown`
4. No grade information → `Unknown`
5. `Mawdu` (fabricated) takes precedence if it is the sole flag from either source.

**Result from validation run**:
| Grade | Count |
|-------|-------|
| Sahih | 26,974 |
| Da'if | 3,026 |
| Hasan | 2,905 |
| Unknown | 558 |
| Mawdu | 28 |

**Implementation**: `backend/scripts/data_creation.py`, function `normalize_grade()`.

---

## Decision 6: Preprocessing Pipeline — Matn, Isnad, and Full Text as Separate Columns

**Date**: Phase 9 (July 2026)  
**Decision**: Preprocess all three text fields (full text, isnad, matn) independently for both languages, storing results in six separate columns: `Preprocessed_English`, `Preprocessed_Arabic`, `Preprocessed_English_Isnad`, `Preprocessed_Arabic_Isnad`, `Preprocessed_English_Matn`, `Preprocessed_Arabic_Matn`.

**Rationale**: Preprocessing all three fields separately preserves optionality for future experiments (e.g., isnad-based retrieval, full-text fallback, isnad fusion). The retrieval pipeline currently uses only the matn columns; the full-text and isnad columns are available for ablation studies without rerunning preprocessing.

**English pipeline**: Lowercasing → punctuation removal → NLTK `word_tokenize` → WordNet lemmatization with POS tagging → stopword removal (NLTK stopwords + custom isnad terms).

**Arabic pipeline**: Unicode range filtering → alef/hamza/teh-marbuta normalization (CAMeL Tools) → tatweel removal → `simple_word_tokenize` → CAMeL MLE disambiguation (calima-msa-r13) → lemma extraction → POS filtering (remove prepositions, conjunctions, particles, punctuation) → custom isnad stopword removal.

**Custom isnad stopwords**:
- English: `say, narrate, told, informed, reported, transmitted, heard, narration, authority, correct, weak, bin, ibn, abu, abi, hadith`
- Arabic (normalized): `قال, حدث, روى, حديث, ضعيف, قوى, صلى, سلم, بن, ابن, ابي, ابو, اخبر`

**Implementation**: `backend/scripts/preprocess.py`.

---

## Corpus Summary Table

| Stage | Rows | Notes |
|-------|------|-------|
| LK raw load | ~34,088 | 6 books, all CSVs concatenated |
| After reconstruction | ~34,088 | No rows added or removed; fields filled in |
| After first-stage drop (missing bilingual matn) | 33,491 | 597 rows dropped; audit in `dropped_lk_rows.json` |
| After second-stage drop (preprocessing collapses to empty) | ~33,064 | ~427 rows dropped; pending pipeline rerun |

The final canonical corpus for all retrieval and evaluation experiments is the result of the second-stage drop.

---

## Reproducibility Notes

- The LK source corpus commit SHA is recorded in `backend/data/build_manifest.json` after each pipeline run via `get_source_commit()` in `data_creation.py`.
- All drop decisions are audited in `backend/data/dropped_lk_rows.json` with per-row metadata (book, hadith number, chapter, reason).
- The build pipeline is fully deterministic given the same LK source commit. No random sampling or model inference is used during data construction.
- The build orchestrator is `backend/scripts/build_all.py`. Running `python scripts/build_all.py --force` from the `backend/` directory reproduces the full corpus and all artifacts from the LK clone.
