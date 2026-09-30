# System Architecture

> Technical reference for the retrieval pipeline architecture, data flow, and component interactions.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Client Browser                        │
│                  React SPA (port 5173 dev)                   │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTP (REST, /api/v1)
┌────────────────────────────▼────────────────────────────────┐
│                    FastAPI Backend (port 8000)                │
│                                                              │
│  ┌──────────┐  ┌────────────┐  ┌────────────┐  ┌────────┐  │
│  │ /searches│  │/assignments│  │/benchmark  │  │/tokens │  │
│  └────┬─────┘  └─────┬──────┘  └─────┬──────┘  └────────┘  │
│       │              │               │                       │
│  ┌────▼──────────────▼───────────────▼──────────────────┐   │
│  │                  scripts/search.py                    │   │
│  │           8 retrieval systems                         │   │
│  └───────────┬──────────────────────┬────────────────────┘   │
│              │                      │                        │
│  ┌───────────▼──────┐  ┌────────────▼────────────────────┐  │
│  │  Sparse Index    │  │  Dense Index                    │  │
│  │  BM25 / TF-IDF   │  │  E5 embeddings (pgvector)       │  │
│  │  (postings SQL)  │  │                                 │  │
│  └───────────┬──────┘  └────────────┬────────────────────┘  │
│              │                      │                        │
│  ┌───────────▼──────────────────────▼────────────────────┐  │
│  │                  PostgreSQL + pgvector                 │  │
│  │   33,064 rows × bilingual matn-complete corpus         │  │
│  └────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Build Pipeline Data Flow

```
LK-Hadith-Corpus/ (CSVs)
        │
        ▼
data_creation.py
  ├── Load 6 books → concatenate (~34,088 rows)
  ├── Reconstruct missing matn/isnad fields (deterministic)
  ├── Drop rows missing bilingual matn (first-stage drop)
  │     └── 597 rows dropped → dropped_lk_rows.json
  ├── Normalize grades
  └── Write → hadiths table (33,491 rows)
        │
        ▼
profile.py (read-only audit, no modifications)
        │
        ▼
preprocess.py
  ├── Preprocess English text, isnad, matn (3 columns)
  ├── Preprocess Arabic text, isnad, matn (3 columns)
  ├── Detect rows where preprocessed matn is empty (second-stage drop candidates)
  └── Write 6 Preprocessed_* columns → hadiths table
        │
        ▼
build_inverted_index.py
  ├── Read Preprocessed_English_Matn, Preprocessed_Arabic_Matn
  ├── Build BM25 postings lists
  └── Replace rows in terms, postings, hadith_lengths
        │
        ▼
build_embeddings.py
  ├── Read English_Matn, Arabic_Matn
  ├── Format: "passage: {matn}" (Arabic: query-side light normalization)
  ├── Encode with intfloat/multilingual-e5-large (CUDA)
  └── Upsert float32 vectors into hadith_embeddings (english, arabic)
        │
        ▼
pooling.py
  ├── Run all retrieval systems over 20 eval queries
  ├── Union of top-k results per query
  └── Write → qrels_ungraded.json
```

---

## Retrieval Data Flow (At Query Time)

```
User query (text string + language)
        │
        ▼
routers/search.py
  ├── Detect language (EN / AR)
  ├── Route to search.py function
  │
  ├── Sparse path:
  │     ├── preprocess_english(query) or preprocess_arabic(query)
  │     ├── Look up terms in inverted index (pkl)
  │     └── Score with BM25 / TF-IDF / Overlap
  │
  └── Dense path:
        ├── Format: "query: {query_text}"
        ├── Encode with E5 model (loading.py LRU cache)
        └── Cosine distance against the embeddings in PostgreSQL (pgvector)
                │
                ▼
        Top-k hadith IDs → fetch from the hadiths table → return JSON
```

---

## Loading and Caching (`loading.py`)

Only the NLP and model objects are loaded lazily and cached with `functools.lru_cache`. The index and the embeddings are database tables, queried per request (`services/ranking.py`).

| Loader | Object | Trigger |
|--------|--------|---------|
| `get_model()` | SentenceTransformer + optional LoRA adapter | First dense search |
| `get_mle()` | CAMeL MLE disambiguator | First Arabic preprocessing |
| `get_english_lemmatizer()` | NLTK WordNetLemmatizer | First English preprocessing |

**LoRA adapter loading**: If `FINETUNED_ADAPTER_PATH` env var is set, `get_model()` loads the base E5 model and applies the PEFT adapter from that path. Otherwise, the base model is loaded as-is.

**Warning**: Changing `FINETUNED_ADAPTER_PATH` at runtime does not invalidate the LRU cache. The server must be restarted to switch adapters.

---

## Database Schema (PostgreSQL)

Primary table: `HADITHS`

```sql
CREATE TABLE hadiths (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    Book                      TEXT,
    Hadith_Number             TEXT,
    Chapter_Number            TEXT,
    Chapter_Title_English     TEXT,
    Chapter_Title_Arabic      TEXT,
    Section_Number            TEXT,
    Section_Title_English     TEXT,
    Section_Title_Arabic      TEXT,
    English_Text              TEXT,
    Arabic_Text               TEXT,
    English_Isnad             TEXT,
    Arabic_Isnad              TEXT,
    English_Matn              TEXT,
    Arabic_Matn               TEXT,
    English_Grade             TEXT,
    Arabic_Grade              TEXT,
    Grade                     TEXT,
    English_Text_Source       TEXT,
    Arabic_Text_Source        TEXT,
    English_Isnad_Source      TEXT,
    Arabic_Isnad_Source       TEXT,
    English_Matn_Source       TEXT,
    Arabic_Matn_Source        TEXT,
    Has_English_Content       INTEGER,
    Has_Arabic_Content        INTEGER,
    Has_English_Matn          INTEGER,
    Has_Arabic_Matn           INTEGER,
    Preprocessed_English      TEXT,
    Preprocessed_Arabic       TEXT,
    Preprocessed_English_Isnad TEXT,
    Preprocessed_Arabic_Isnad  TEXT,
    Preprocessed_English_Matn  TEXT,
    Preprocessed_Arabic_Matn   TEXT
);

CREATE UNIQUE INDEX idx_hadiths_id   ON hadiths(id);
CREATE INDEX idx_hadiths_book        ON hadiths(Book);
CREATE INDEX idx_hadiths_grade       ON hadiths(Grade);
```

Annotation tables (added by `database.py`):

```sql
CREATE TABLE users (id, username, hashed_password, created_at)
CREATE TABLE query_assignments (id, user_id, query_id, assigned_at)
CREATE TABLE relevance_grades (id, user_id, query_id, hadith_id, grade, graded_at)
CREATE TABLE kv_pairs (id, concept, hadith_id, verified, created_at)
```

---

## Alignment Invariant

Embeddings, postings and lengths are keyed by `hadith_id` with a foreign key to `hadiths.id` and `ON DELETE CASCADE`, so they cannot point at a hadith that is gone. Changing the corpus (rows added or removed) means re-running `build_inverted_index.py` (its `write_index` replaces the index rows) and `build_embeddings.py` (it upserts vectors, so rows for new hadiths are added; rows for removed ones are deleted with the hadith). `hadith_embeddings` has no fixed vector dimension, so switching the embedding model needs no migration, but every row must be re-encoded with the same model.
