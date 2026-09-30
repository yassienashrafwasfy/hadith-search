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
│  │           11 retrieval systems                        │   │
│  └───────────┬──────────────────────┬────────────────────┘   │
│              │                      │                        │
│  ┌───────────▼──────┐  ┌────────────▼────────────────────┐  │
│  │  Sparse Index    │  │  Dense Index                    │  │
│  │  BM25 / TF-IDF   │  │  E5 embeddings (npy)            │  │
│  │  (pkl files)     │  │  + Jina reranker API            │  │
│  └───────────┬──────┘  └────────────┬────────────────────┘  │
│              │                      │                        │
│  ┌───────────▼──────────────────────▼────────────────────┐  │
│  │                   hadiths.db (SQLite)                  │  │
│  │   33,064 rows × bilingual matn-complete corpus         │  │
│  └────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                             │
              ┌──────────────▼──────────────┐
              │       Jina AI API           │
              │  jina-reranker-v3           │
              │  (external, rate-limited)   │
              └─────────────────────────────┘
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
  └── Write → hadiths.db (33,491 rows)
        │
        ▼
profile.py (read-only audit, no modifications)
        │
        ▼
preprocess.py
  ├── Preprocess English text, isnad, matn (3 columns)
  ├── Preprocess Arabic text, isnad, matn (3 columns)
  ├── Detect rows where preprocessed matn is empty (second-stage drop candidates)
  └── Write 6 Preprocessed_* columns → hadiths.db
        │
        ▼
build_inverted_index.py
  ├── Read Preprocessed_English_Matn, Preprocessed_Arabic_Matn
  ├── Build BM25 postings lists
  └── Write → english_inverted_index.pkl
              arabic_inverted_index.pkl
              document_lengths.pkl
        │
        ▼
build_embeddings.py
  ├── Read English_Matn, Arabic_Matn
  ├── Format: "passage: {matn}" (Arabic: query-side light normalization)
  ├── Encode with intfloat/multilingual-e5-large (CUDA)
  └── Write → english_embeddings.npy
              arabic_embeddings.npy
              hadith_ids.npy
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
        ├── Cosine similarity against embedding array (npy)
        └── Optional: rerank with Jina API
                │
                ▼
        Top-k hadith IDs → fetch from hadiths.db → return JSON
```

---

## Loading and Caching (`loading.py`)

All heavy objects are loaded lazily and cached with `functools.lru_cache`:

| Loader | Object | Trigger |
|--------|--------|---------|
| `get_english_inverted_index()` | BM25 postings (pkl) | First sparse EN search |
| `get_arabic_inverted_index()` | BM25 postings (pkl) | First sparse AR search |
| `get_document_lengths()` | Document length dict (pkl) | First BM25 search |
| `get_english_embeddings()` | EN embedding array (npy) | First dense search |
| `get_arabic_embeddings()` | AR embedding array (npy) | First dense search |
| `get_hadith_ids()` | ID alignment array (npy) | First dense search |
| `get_e5_model()` | SentenceTransformer + optional LoRA adapter | First dense search |
| `get_mle()` | CAMeL MLE disambiguator | First Arabic preprocessing |
| `get_english_lemmatizer()` | NLTK WordNetLemmatizer | First English preprocessing |

**LoRA adapter loading**: If `FINETUNED_ADAPTER_PATH` env var is set, `get_e5_model()` loads the base E5 model and applies the PEFT adapter from that path. Otherwise, the base model is loaded as-is.

**Warning**: Changing `FINETUNED_ADAPTER_PATH` at runtime does not invalidate the LRU cache. The server must be restarted to switch adapters.

---

## Database Schema (`hadiths.db`)

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

`hadith_ids.npy`, `english_embeddings.npy`, and `arabic_embeddings.npy` must always have the same length and be generated from the same ordered query of `hadiths.db`. The mapping `hadith_ids[i] → embeddings[i]` is used to translate cosine similarity rank positions back to database IDs.

This invariant is asserted at the end of `build_embeddings.py`. Any change to the corpus (rows added or removed) requires regenerating all three files together.

---

## APP_MODE Conditional Loading

`main.py` uses `APP_MODE` to conditionally include routers:

```python
if os.getenv("APP_MODE") != "annotation":
    app.include_router(search_router)
    app.include_router(benchmark_router)
```

This ensures the annotation-only deployment never triggers E5 model import or BM25 index load, keeping RAM usage minimal (~200MB vs ~3GB loaded).
